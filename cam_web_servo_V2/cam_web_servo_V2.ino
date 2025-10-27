/*
  ESP32 + RHYX M21 (GC2145) camera + IDF HTTP server + Servos + Simple UI
  + Login (cookie session, bắt đổi mật khẩu lần đầu, lưu NVS) [login.h]
  + Provisioning Wi-Fi (AP/STA, mDNS .local, trang /wifi & /qr) [net_cfg.h]

  Endpoints:
  - /login (GET/POST), /logout, /first-change                  [login.h]
  - /wifi (GET/POST), /qr                                      [net_cfg.h]
  - /ui (web UI), /stream (MJPEG: :81), /capture (JPEG), /bmp (BMP)
  - /status (JSON), /control (sensor params), /xclk, /reg, /greg, /pll, /resolution
  - /led?val=0|1, /servo?ch=1|2&val=0..180

  Yêu cầu:
  - ESP32 core for Arduino (Board: "ESP32 Wrover Module" hoặc "AI Thinker ESP32-CAM", PSRAM On)
  - Library: ESP32Servo (Kevin Harrington / John K. Bennett)
  - Partition scheme: tối thiểu "Huge APP" (3MB APP)
*/

#include <WiFi.h>                 // chỉ dùng của core
#include "esp_camera.h"

#include "esp_http_server.h"
#include "esp_timer.h"
#include "img_converters.h"
#include "fb_gfx.h"
#include "esp32-hal-ledc.h"
#include "sdkconfig.h"

#include <ESP32Servo.h>
#include "login.h"    // Auth + bắt đổi mật khẩu lần đầu (NVS)
#include "net_cfg.h"  // AP/STA + mDNS + /wifi + /qr
#include "web_ui.h"   // UI tại /ui

/************ CAMERA PINS (AI-Thinker map) ************/
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

/************ PERIPHERALS ************/
#define LED_FLASH_PIN      4        // on-board flash (transistor)
#define LED_PWM_ENABLED    0        // set 1 để PWM brightness

#define SERVO1_PIN         12       // ⚠ strapping pin; rút dây khi boot nếu bị kẹt
#define SERVO2_PIN         13

/************ GLOBALS ************/
httpd_handle_t stream_httpd = NULL;
httpd_handle_t camera_httpd = NULL;

Servo servo1, servo2;
int servo1_angle = 90, servo2_angle = 90;

#if LED_PWM_ENABLED
static int led_duty = 0;
static bool isStreaming = false;
#endif

/************ CAMERA INIT (GC2145 -> RGB565 + SW JPEG) ************/
static void camera_init_gc2145() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;          // GC2145 ổn định ở 20MHz
  config.pixel_format = PIXFORMAT_RGB565;  // không có HW JPEG

  if (psramFound()) {
    config.frame_size   = FRAMESIZE_VGA;   // 640x480
    config.fb_location  = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240
    config.fb_location  = CAMERA_FB_IN_DRAM;
  }

  config.jpeg_quality = 12;                // cho SW JPEG
  config.fb_count     = 1;
  config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    while (true) delay(1000);
  }

  sensor_t *s = esp_camera_sensor_get();
  Serial.printf("Sensor PID: 0x%04x\n", s ? s->id.PID : 0xffff);

  // Mặc định thân thiện cho GC2145 (RHYX M21)
  if (s) {
    s->set_vflip(s, 1);
    s->set_hmirror(s, 1);
    s->set_brightness(s, 1);
    s->set_contrast(s, 1);
    s->set_saturation(s, -1);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_aec2(s, 1);
    s->set_gain_ctrl(s, 1);
    s->set_exposure_ctrl(s, 1);
  }
}

/************ LED CONTROL ************/
static inline void flash_led_set(bool on) {
#if LED_PWM_ENABLED
  int duty = on ? led_duty : 0;
  ledcWrite(LED_FLASH_PIN, duty);
#else
  digitalWrite(LED_FLASH_PIN, on ? HIGH : LOW);
#endif
}

/************ HTTP HELPERS (IDF server) ************/
typedef struct {
  httpd_req_t *req;
  size_t len;
} jpg_chunking_t;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY     = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART         = "Content-Type: image/jpeg\r\nContent-Length: %u\r\nX-Timestamp: %d.%06d\r\n\r\n";

static size_t jpg_encode_stream(void *arg, size_t index, const void *data, size_t len) {
  jpg_chunking_t *j = (jpg_chunking_t*)arg;
  if (!index) j->len = 0;
  if (httpd_resp_send_chunk(j->req, (const char*)data, len) != ESP_OK) return 0;
  j->len += len;
  return len;
}

/************ HANDLERS: /bmp ************/
static esp_err_t bmp_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/x-windows-bmp");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.bmp");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  uint8_t *buf = NULL; size_t len = 0;
  bool ok = frame2bmp(fb, &buf, &len);
  esp_camera_fb_return(fb);
  if (!ok) { httpd_resp_send_500(req); return ESP_FAIL; }
  esp_err_t res = httpd_resp_send(req, (const char*)buf, len);
  free(buf);
  return res;
}

/************ HANDLERS: /capture (JPEG) ************/
static esp_err_t capture_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  camera_fb_t *fb;
#if LED_PWM_ENABLED
  flash_led_set(true); vTaskDelay(150 / portTICK_PERIOD_MS);
#endif
  fb = esp_camera_fb_get();
#if LED_PWM_ENABLED
  flash_led_set(false);
#endif
  if (!fb) { httpd_resp_send_500(req); return ESP_FAIL; }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  esp_err_t res = ESP_OK;
  if (fb->format == PIXFORMAT_JPEG) {
    res = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  } else {
    jpg_chunking_t j = { req, 0 };
    res = frame2jpg_cb(fb, 80, jpg_encode_stream, &j) ? ESP_OK : ESP_FAIL;
    httpd_resp_send_chunk(req, NULL, 0);
  }
  esp_camera_fb_return(fb);
  return res;
}

/************ HANDLERS: /stream (MJPEG) ************/
static esp_err_t stream_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

#if LED_PWM_ENABLED
  isStreaming = true; flash_led_set(true);
#endif

  while (true) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;

    uint8_t *jpg_buf = NULL; size_t jpg_len = 0;
    struct timeval ts = fb->timestamp;

    if (fb->format != PIXFORMAT_JPEG) {
      if (!frame2jpg(fb, 80, &jpg_buf, &jpg_len)) {
        esp_camera_fb_return(fb);
        res = ESP_FAIL; break;
      }
      esp_camera_fb_return(fb);
    } else {
      jpg_buf = fb->buf; jpg_len = fb->len;
      esp_camera_fb_return(fb);
    }

    if ((res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY))) != ESP_OK) break;

    char hdr[128];
    size_t hlen = snprintf(hdr, sizeof(hdr), STREAM_PART, jpg_len, (int)ts.tv_sec, (int)ts.tv_usec);
    if ((res = httpd_resp_send_chunk(req, hdr, hlen)) != ESP_OK) break;
    if ((res = httpd_resp_send_chunk(req, (const char*)jpg_buf, jpg_len)) != ESP_OK) break;

    if (jpg_buf && fb->format != PIXFORMAT_JPEG) free(jpg_buf);
    vTaskDelay(30 / portTICK_PERIOD_MS);
  }

#if LED_PWM_ENABLED
  isStreaming = false; flash_led_set(false);
#endif

  return res;
}

/************ CONTROL + STATUS ************/
static esp_err_t status_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  sensor_t *s = esp_camera_sensor_get();
  if (!s) { return httpd_resp_send_500(req); }
  static char json[512];
  int n = snprintf(json, sizeof(json),
    "{\"xclk\":%u,\"pixformat\":%u,\"framesize\":%u,"
    "\"quality\":%u,\"brightness\":%d,\"contrast\":%d,\"saturation\":%d,"
    "\"awb\":%u,\"awb_gain\":%u,\"aec\":%u,\"aec2\":%u,\"ae_level\":%d,"
    "\"aec_value\":%u,\"agc\":%u,\"agc_gain\":%u,\"gainceiling\":%u,"
    "\"bpc\":%u,\"wpc\":%u,\"raw_gma\":%u,\"lenc\":%u,"
    "\"hmirror\":%u,\"vflip\":%u,\"dcw\":%u,\"colorbar\":%u}",
    s->xclk_freq_hz/1000000, s->pixformat, s->status.framesize,
    s->status.quality, s->status.brightness, s->status.contrast, s->status.saturation,
    s->status.awb, s->status.awb_gain, s->status.aec, s->status.aec2, s->status.ae_level,
    s->status.aec_value, s->status.agc, s->status.agc_gain, s->status.gainceiling,
    s->status.bpc, s->status.wpc, s->status.raw_gma, s->status.lenc,
    s->status.hmirror, s->status.vflip, s->status.dcw, s->status.colorbar
  );
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json, n);
}

static esp_err_t parse_get(httpd_req_t *req, char **obuf) {
  size_t len = httpd_req_get_url_query_len(req) + 1;
  if (len <= 1) return ESP_FAIL;
  char *buf = (char*)malloc(len);
  if (!buf) return ESP_FAIL;
  if (httpd_req_get_url_query_str(req, buf, len) != ESP_OK) { free(buf); return ESP_FAIL; }
  *obuf = buf; return ESP_OK;
}

static esp_err_t cmd_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf = NULL; char variable[32]; char value[32];
  if (parse_get(req, &buf) != ESP_OK) return ESP_FAIL;
  if (httpd_query_key_value(buf, "var", variable, sizeof(variable)) != ESP_OK ||
      httpd_query_key_value(buf, "val", value, sizeof(value)) != ESP_OK) {
    free(buf); httpd_resp_send_404(req); return ESP_FAIL;
  }
  int val = atoi(value);
  sensor_t *s = esp_camera_sensor_get(); int res = 0;

  if      (!strcmp(variable, "framesize"))  { res = s->set_framesize(s, (framesize_t)val); }
  else if (!strcmp(variable, "quality"))    { res = s->set_quality(s, val); }
  else if (!strcmp(variable, "contrast"))   { res = s->set_contrast(s, val); }
  else if (!strcmp(variable, "brightness")) { res = s->set_brightness(s, val); }
  else if (!strcmp(variable, "saturation")) { res = s->set_saturation(s, val); }
  else if (!strcmp(variable, "awb"))        { res = s->set_whitebal(s, val); }
  else if (!strcmp(variable, "awb_gain"))   { res = s->set_awb_gain(s, val); }
  else if (!strcmp(variable, "agc"))        { res = s->set_gain_ctrl(s, val); }
  else if (!strcmp(variable, "agc_gain"))   { res = s->set_agc_gain(s, val); }
  else if (!strcmp(variable, "aec"))        { res = s->set_exposure_ctrl(s, val); }
  else if (!strcmp(variable, "aec2"))       { res = s->set_aec2(s, val); }
  else if (!strcmp(variable, "ae_level"))   { res = s->set_ae_level(s, val); }
  else if (!strcmp(variable, "aec_value"))  { res = s->set_aec_value(s, val); }
  else if (!strcmp(variable, "hmirror"))    { res = s->set_hmirror(s, val); }
  else if (!strcmp(variable, "vflip"))      { res = s->set_vflip(s, val); }
  else if (!strcmp(variable, "dcw"))        { res = s->set_dcw(s, val); }
  else if (!strcmp(variable, "bpc"))        { res = s->set_bpc(s, val); }
  else if (!strcmp(variable, "wpc"))        { res = s->set_wpc(s, val); }
  else if (!strcmp(variable, "raw_gma"))    { res = s->set_raw_gma(s, val); }
  else if (!strcmp(variable, "lenc"))       { res = s->set_lenc(s, val); }
#if LED_PWM_ENABLED
  else if (!strcmp(variable, "led_intensity")) {
    led_duty = val; if (isStreaming) flash_led_set(true);
  }
#endif
  else { res = -1; }

  free(buf);
  if (res < 0) return httpd_resp_send_500(req);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, NULL, 0);
}

static int parse_int(char *buf, const char *key, int def) {
  char tmp[16]; if (httpd_query_key_value(buf, key, tmp, sizeof(tmp)) != ESP_OK) return def; return atoi(tmp);
}

static esp_err_t xclk_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  char x[16]; if (httpd_query_key_value(buf, "xclk", x, sizeof(x))!=ESP_OK) { free(buf); httpd_resp_send_404(req); return ESP_FAIL; }
  int mhz = atoi(x); sensor_t *s = esp_camera_sensor_get();
  int r = s->set_xclk(s, LEDC_TIMER_0, mhz);
  free(buf); if (r) return httpd_resp_send_500(req);
  httpd_resp_set_hdr(req,"Access-Control-Allow-Origin","*"); return httpd_resp_send(req,NULL,0);
}

static esp_err_t reg_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  char _reg[16], _mask[16], _val[16];
  if (httpd_query_key_value(buf,"reg",_reg,sizeof(_reg))!=ESP_OK ||
      httpd_query_key_value(buf,"mask",_mask,sizeof(_mask))!=ESP_OK ||
      httpd_query_key_value(buf,"val",_val,sizeof(_val))!=ESP_OK) { free(buf); httpd_resp_send_404(req); return ESP_FAIL; }
  int r = atoi(_reg), m = atoi(_mask), v = atoi(_val);
  sensor_t *s = esp_camera_sensor_get(); int res = s->set_reg(s, r, m, v);
  free(buf); if (res) return httpd_resp_send_500(req);
  httpd_resp_set_hdr(req,"Access-Control-Allow-Origin","*"); return httpd_resp_send(req,NULL,0);
}

static esp_err_t greg_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  char _reg[16], _mask[16];
  if (httpd_query_key_value(buf,"reg",_reg,sizeof(_reg))!=ESP_OK ||
      httpd_query_key_value(buf,"mask",_mask,sizeof(_mask))!=ESP_OK) { free(buf); httpd_resp_send_404(req); return ESP_FAIL; }
  int r = atoi(_reg), m = atoi(_mask);
  sensor_t *s = esp_camera_sensor_get(); int val = s->get_reg(s, r, m);
  free(buf); if (val<0) return httpd_resp_send_500(req);
  char out[20]; itoa(val, out, 10);
  httpd_resp_set_hdr(req,"Access-Control-Allow-Origin","*");
  return httpd_resp_send(req, out, strlen(out));
}

static esp_err_t pll_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  int bypass=parse_int(buf,"bypass",0), mul=parse_int(buf,"mul",0), sys=parse_int(buf,"sys",0),
      root=parse_int(buf,"root",0), pre=parse_int(buf,"pre",0), seld5=parse_int(buf,"seld5",0),
      pclken=parse_int(buf,"pclken",0), pclk=parse_int(buf,"pclk",0);
  sensor_t *s = esp_camera_sensor_get();
  int res = s->set_pll(s, bypass, mul, sys, root, pre, seld5, pclken, pclk);
  free(buf); if (res) return httpd_resp_send_500(req);
  httpd_resp_set_hdr(req,"Access-Control-Allow-Origin","*"); return httpd_resp_send(req,NULL,0);
}

static esp_err_t win_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  int sx=parse_int(buf,"sx",0), sy=parse_int(buf,"sy",0), ex=parse_int(buf,"ex",0), ey=parse_int(buf,"ey",0),
      offx=parse_int(buf,"offx",0), offy=parse_int(buf,"offy",0), tx=parse_int(buf,"tx",0), ty=parse_int(buf,"ty",0),
      ox=parse_int(buf,"ox",0), oy=parse_int(buf,"oy",0), scale=parse_int(buf,"scale",0), binning=parse_int(buf,"binning",0);
  sensor_t *s = esp_camera_sensor_get();
  int res = s->set_res_raw(s, sx, sy, ex, ey, offx, offy, tx, ty, ox, oy, scale==1, binning==1);
  free(buf); if (res) return httpd_resp_send_500(req);
  httpd_resp_set_hdr(req,"Access-Control-Allow-Origin","*"); return httpd_resp_send(req,NULL,0);
}

/************ EXTRA: /ui HTML ************/
static esp_err_t ui_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, (const char*)PAGE_HTML, strlen(PAGE_HTML));
}

/************ EXTRA: /led (0/1) ************/
static esp_err_t led_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  char v[8]; if (httpd_query_key_value(buf,"val",v,sizeof(v))!=ESP_OK) { free(buf); httpd_resp_send_404(req); return ESP_FAIL; }
  bool on = atoi(v) >= 1;
  flash_led_set(on);
  free(buf);
  return httpd_resp_send(req, on ? "1" : "0", 1);
}

/************ EXTRA: /servo ************/
static esp_err_t servo_handler(httpd_req_t *req) {
  REQUIRE_LOGIN_OR_REDIRECT(req);
  char *buf=NULL; if (parse_get(req,&buf)!=ESP_OK) return ESP_FAIL;
  char chs[8], vals[8];
  if (httpd_query_key_value(buf,"ch",chs,sizeof(chs))!=ESP_OK ||
      httpd_query_key_value(buf,"val",vals,sizeof(vals))!=ESP_OK) { free(buf); httpd_resp_send_404(req); return ESP_FAIL; }
  int ch = atoi(chs); int val = atoi(vals); if (val<0) val=0; if (val>180) val=180;
  if (ch==1) { servo1_angle=val; servo1.write(val); }
  else if (ch==2) { servo2_angle=val; servo2.write(val); }
  else { free(buf); return httpd_resp_send_500(req); }
  free(buf);
  char out[32]; snprintf(out,sizeof(out),"{\"ch\":%d,\"angle\":%d}",ch,val);
  httpd_resp_set_type(req,"application/json");
  return httpd_resp_send(req, out, strlen(out));
}

/************ SERVER START ************/
static void start_server() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.max_uri_handlers = 32;

  // main server (:80)
  if (httpd_start(&camera_httpd, &config) == ESP_OK) {
    // Đăng ký route đăng nhập & wifi (có guard riêng bên trong)
    auth::register_routes(camera_httpd);     // /login, /logout, /first-change
    wifi_cfg::register_routes(camera_httpd); // /wifi, /qr

    // App routes (đều dùng REQUIRE_LOGIN_OR_REDIRECT ở đầu handler)
    httpd_uri_t ui_uri       = { .uri="/ui",        .method=HTTP_GET, .handler=ui_handler,       .user_ctx=NULL };
    httpd_uri_t cap_uri      = { .uri="/capture",   .method=HTTP_GET, .handler=capture_handler,  .user_ctx=NULL };
    httpd_uri_t bmp_uri      = { .uri="/bmp",       .method=HTTP_GET, .handler=bmp_handler,      .user_ctx=NULL };
    httpd_uri_t status_uri   = { .uri="/status",    .method=HTTP_GET, .handler=status_handler,   .user_ctx=NULL };
    httpd_uri_t control_uri  = { .uri="/control",   .method=HTTP_GET, .handler=cmd_handler,      .user_ctx=NULL };
    httpd_uri_t xclk_uri     = { .uri="/xclk",      .method=HTTP_GET, .handler=xclk_handler,     .user_ctx=NULL };
    httpd_uri_t reg_uri      = { .uri="/reg",       .method=HTTP_GET, .handler=reg_handler,      .user_ctx=NULL };
    httpd_uri_t greg_uri     = { .uri="/greg",      .method=HTTP_GET, .handler=greg_handler,     .user_ctx=NULL };
    httpd_uri_t pll_uri      = { .uri="/pll",       .method=HTTP_GET, .handler=pll_handler,      .user_ctx=NULL };
    httpd_uri_t win_uri      = { .uri="/resolution",.method=HTTP_GET, .handler=win_handler,      .user_ctx=NULL };
    httpd_uri_t led_uri      = { .uri="/led",       .method=HTTP_GET, .handler=led_handler,      .user_ctx=NULL };
    httpd_uri_t servo_uri    = { .uri="/servo",     .method=HTTP_GET, .handler=servo_handler,    .user_ctx=NULL };
    httpd_register_uri_handler(camera_httpd, &ui_uri);
    httpd_register_uri_handler(camera_httpd, &cap_uri);
    httpd_register_uri_handler(camera_httpd, &bmp_uri);
    httpd_register_uri_handler(camera_httpd, &status_uri);
    httpd_register_uri_handler(camera_httpd, &control_uri);
    httpd_register_uri_handler(camera_httpd, &xclk_uri);
    httpd_register_uri_handler(camera_httpd, &reg_uri);
    httpd_register_uri_handler(camera_httpd, &greg_uri);
    httpd_register_uri_handler(camera_httpd, &pll_uri);
    httpd_register_uri_handler(camera_httpd, &win_uri);
    httpd_register_uri_handler(camera_httpd, &led_uri);
    httpd_register_uri_handler(camera_httpd, &servo_uri);

    // Gán "/" → /ui nếu muốn:
    // httpd_uri_t root_uri = { .uri="/", .method=HTTP_GET, .handler=ui_handler, .user_ctx=NULL };
    // httpd_register_uri_handler(camera_httpd, &root_uri);
  }

  // stream server (:81)
  config.server_port += 1; config.ctrl_port += 1;
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_uri_t stream_uri = { .uri="/stream", .method=HTTP_GET, .handler=stream_handler, .user_ctx=NULL };
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

/************ SETUP / LOOP ************/
void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  // LED pin
#if LED_PWM_ENABLED
  ledcAttach(LED_FLASH_PIN, 5000, 8);
  led_duty = 64;
#else
  pinMode(LED_FLASH_PIN, OUTPUT);
  digitalWrite(LED_FLASH_PIN, LOW);
#endif

  // Servos (50 Hz)
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  ESP32PWM::allocateTimer(4);
  servo1.setPeriodHertz(50);
  servo2.setPeriodHertz(50);
  servo1.attach(SERVO1_PIN, 500, 2500);
  servo2.attach(SERVO2_PIN, 500, 2500);
  servo1.write(servo1_angle);
  servo2.write(servo2_angle);

  // Camera -> nên init TRƯỚC khi mở server
  camera_init_gc2145();

  // Auth (user/pass) + Network (AP/STA + mDNS)
  auth::init();            // nạp NVS, chuẩn bị login
  wifi_cfg::begin();       // chọn AP/STA + mDNS (phải trước server)

  wifi_cfg::init_and_begin();
  wifi_cfg::print_status();

  // HTTP servers
  start_server();                            // khởi động HTTP/stream server
  auth::register_routes(camera_httpd);       // /login, /first-change, /logout
  wifi_cfg::register_routes(camera_httpd);   // /wifi, /wifi/status, /wifi/scan, /qr

  // Log địa chỉ truy cập
  if (WiFi.getMode() & WIFI_MODE_AP) {
    Serial.printf("AP IP:  %s\n", WiFi.softAPIP().toString().c_str());
  }
  if (WiFi.getMode() & WIFI_MODE_STA) {
    Serial.printf("STA IP: %s\n", WiFi.localIP().toString().c_str());
  }
  const char* ipShown = (WiFi.getMode() & WIFI_MODE_STA)
                        ? WiFi.localIP().toString().c_str()
                        : WiFi.softAPIP().toString().c_str();
  Serial.printf("UI:     http://%s/ui\n", ipShown);
  Serial.printf("Stream: http://%s:81/stream\n", ipShown);
}

void loop() {
  // all handled in http server tasks
  delay(10000);
}