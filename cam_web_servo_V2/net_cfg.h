#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "esp_http_server.h"

// ===== Guard macro (neu login.h chua dinh nghia) =====
#ifndef REQUIRE_LOGIN_OR_REDIRECT
#define REQUIRE_LOGIN_OR_REDIRECT(req) do{}while(0)
#endif

// ===== Shim lay thong tin tu login.h =====
extern "C" const char* auth_current_user();
extern "C" const char* auth_csrf_token(); // co the tra "" neu ban tat CSRF

namespace wifi_cfg {

// ================== STATE ==================
static Preferences pref;
static String cfg_ssid;     // WiFi SSID (STA)
static String cfg_pass;     // WiFi password (STA)
static String cfg_host;     // mDNS hostname, vd: esp32cam  => http://esp32cam.local/

static bool   started_ap = false;
static bool   started_sta = false;

static String ap_ssid;      // ESP32CAM-Setup-XXXX
static String ap_pass = "12345678"; // it nhat 8 ky tu cho AP

// ================== UTILS ==================
static inline uint8_t from_hex(char c){
  if (c>='0' && c<='9') return (uint8_t)(c-'0');
  if (c>='a' && c<='f') return (uint8_t)(10 + (c-'a'));
  if (c>='A' && c<='F') return (uint8_t)(10 + (c-'A'));
  return 0;
}

// parser form application/x-www-form-urlencoded
static void url_decode_inplace(char* s){
  char* r=s; char* w=s;
  while(*r){
    if(*r=='+'){*w++=' '; r++;}
    else if(*r=='%' && r[1] && r[2]){
      uint8_t v=(from_hex(r[1])<<4)|from_hex(r[2]);
      *w++=(char)v; r+=3;
    }else{ *w++=*r++; }
  }
  *w=0;
}

static void parse_form_kv(const char* body, const char* key, char* out, size_t outlen){
  if(!body||!key||!out||outlen==0){ if(outlen) out[0]=0; return; }
  size_t klen=strlen(key);
  const char* p=body; out[0]=0;
  while(*p){
    if(strncmp(p,key,klen)==0 && p[klen]=='='){
      p+=klen+1;
      const char* q=p; while(*q && *q!='&') q++;
      size_t len=(size_t)(q-p); if(len>=outlen) len=outlen-1;
      memcpy(out,p,len); out[len]=0;
      url_decode_inplace(out);
      return;
    }
    while(*p && *p!='&') p++;
    if(*p=='&') p++;
  }
}

static String mac_suffix(){
  uint8_t m[6]; WiFi.macAddress(m);
  char s[8]; snprintf(s,sizeof(s),"%02X%02X", m[4], m[5]);
  return String(s);
}

static void nvs_load(){
  pref.begin("net", true);
  cfg_ssid = pref.getString("ssid", "");
  cfg_pass = pref.getString("pass", "");
  cfg_host = pref.getString("host", "");
  pref.end();
}

static void nvs_save(const String& ssid, const String& pass, const String& host){
  pref.begin("net", false);
  pref.putString("ssid", ssid);
  pref.putString("pass", pass);
  pref.putString("host", host);
  pref.end();
  cfg_ssid = ssid; cfg_pass = pass; cfg_host = host;
}

// ================== mDNS ==================
static void start_mdns_if_any(){
  if(cfg_host.length()==0) return;
  WiFi.setHostname(cfg_host.c_str()); // dat hostname cho STA
  if(!MDNS.begin(cfg_host.c_str())){
    Serial.println("[net] mDNS begin failed");
    return;
  }
  MDNS.addService("http","tcp",80);
  // neu ban muon cong 81 cho stream:
  MDNS.addService("stream","tcp",81);
  Serial.printf("[net] mDNS ok: http://%s.local/\n", cfg_host.c_str());
}

// ================== AP / STA ==================
static void start_ap(){
  if(started_ap) return;
  WiFi.mode(WIFI_AP);
  ap_ssid = "ESP32CAM-Setup-" + mac_suffix();
  bool ok = WiFi.softAP(ap_ssid.c_str(), ap_pass.c_str());
  started_ap = ok;
  IPAddress ip = WiFi.softAPIP();
  Serial.printf("[net] AP %s %s | IP: %s\n", ok?"OK":"FAIL", ap_ssid.c_str(), ip.toString().c_str());
}

static bool try_sta_connect(uint32_t timeout_ms=15000){
  WiFi.mode(WIFI_STA);
  if(cfg_host.length()) WiFi.setHostname(cfg_host.c_str());
  WiFi.begin(cfg_ssid.c_str(), cfg_pass.c_str());
  Serial.printf("[net] STA connecting to '%s'...\n", cfg_ssid.c_str());

  uint32_t t0 = millis();
  while(WiFi.status()!=WL_CONNECTED && (millis()-t0)<timeout_ms){
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  if(WiFi.status()==WL_CONNECTED){
    Serial.printf("[net] STA OK: IP=%s  GW=%s\n",
      WiFi.localIP().toString().c_str(),
      WiFi.gatewayIP().toString().c_str());
    return true;
  }
  Serial.println("[net] STA FAIL");
  return false;
}

static void begin(){
  nvs_load();
  if(cfg_ssid.length()==0){
    // chua co config -> phat AP
    start_ap();
    return;
  }
  // co config -> thu STA, that bai -> AP
  if(try_sta_connect()){
    started_sta = true;
    start_mdns_if_any();
  }else{
    start_ap();
  }
}

// ============= HTML builders (khong dau) =============
static String html_wifi_form(const char* token){
  String h =
"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>Cau hinh Wi-Fi</title>"
"<style>body{font-family:sans-serif;margin:24px}form{max-width:420px;padding:16px;border:1px solid #ddd;border-radius:12px;box-shadow:0 2px 6px rgba(0,0,0,.05)}input,button{width:100%;padding:.6rem;margin:.4rem 0;border-radius:10px;border:1px solid #ccc}button{cursor:pointer}</style>"
"</head><body><h2>Cau hinh Wi-Fi</h2>"
"<form method='POST' action='/wifi'>"
"<input type='hidden' name='csrf_token' value='";
  h += (token?token:"");
  h += "'>"
"<label>SSID</label><input name='ssid' required value='";
  h += cfg_ssid;
  h += "'>"
"<label>Password</label><input type='password' name='pass' value='";
  h += cfg_pass;
  h += "'>"
"<label>Hostname (.local)</label><input name='host' placeholder='esp32cam' value='";
  h += cfg_host;
  h += "'>"
"<button type='submit'>Luu va khoi dong lai</button>"
"</form>"
"<p><a href='/ui'>Tro ve UI</a></p>"
"</body></html>";
  return h;
}

static String html_qr_page(const String& url, const String& user){
  String h =
"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>Ma QR ket noi</title>"
"<style>body{font-family:sans-serif;margin:24px;max-width:760px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}"
"input{width:100%;padding:.6rem;border:1px solid #ccc;border-radius:10px}"
"button{padding:.5rem .9rem;border:1px solid #ccc;border-radius:10px;background:#f6f6f6;cursor:pointer}"
".mono{font-family:monospace}</style>"
"</head><body><h2>Ma QR ket noi</h2>"
"<p>QR chi chua URL va username. Mat khau khong nam trong QR.</p>"
"<div class='row'><input id='txt' class='mono' readonly value='URL: ";
  h += url;
  h += "  |  user: ";
  h += user;
  h += "'><button onclick='navigator.clipboard.writeText(document.getElementById(\"txt\").value)'>Copy</button></div>"
"<p><a href='/ui'>Tro ve UI</a></p>"
"</body></html>";
  return h;
}

// ============= HTTP handlers =============
static esp_err_t wifi_get_handler(httpd_req_t *req){
  REQUIRE_LOGIN_OR_REDIRECT(req);
  const char* tok = auth_csrf_token(); if(!tok) tok="";
  String page = html_wifi_form(tok);
  httpd_resp_set_type(req,"text/html; charset=utf-8");
  return httpd_resp_send(req, page.c_str(), HTTPD_RESP_USE_STRLEN);
}

static esp_err_t wifi_post_handler(httpd_req_t *req){
  REQUIRE_LOGIN_OR_REDIRECT(req);

  int total=req->content_len;
  if(total<=0 || total>1024) return httpd_resp_send_500(req);
  char *buf=(char*)malloc(total+1);
  if(!buf) return httpd_resp_send_500(req);
  int recvd=0;
  while(recvd<total){
    int r=httpd_req_recv(req, buf+recvd, total-recvd);
    if(r<=0){ free(buf); return httpd_resp_send_500(req); }
    recvd+=r;
  }
  buf[recvd]=0;

  char token[96]="", ssid[64]="", pass[64]="", host[64]="";
  parse_form_kv(buf,"csrf_token",token,sizeof(token)); // khong bat buoc
  parse_form_kv(buf,"ssid",ssid,sizeof(ssid));
  parse_form_kv(buf,"pass",pass,sizeof(pass));
  parse_form_kv(buf,"host",host,sizeof(host));
  free(buf);

  if(strlen(ssid)==0){
    httpd_resp_set_status(req,"302 Found");
    httpd_resp_set_hdr(req,"Location","/wifi");
    return httpd_resp_send(req,"Missing SSID",12);
  }

  // luu NVS
  nvs_save(String(ssid), String(pass), String(host));
  Serial.printf("[net] saved: ssid='%s', host='%s'\n", ssid, host);

  // thong bao va restart
  const char* msg =
    "<!doctype html><html><meta charset='utf-8'><body>"
    "<h3>Da luu, thiet bi se khoi dong lai trong 2s...</h3>"
    "<script>setTimeout(()=>{location.href='/'},2000)</script>"
    "</body></html>";
  httpd_resp_set_type(req,"text/html; charset=utf-8");
  httpd_resp_send(req, msg, HTTPD_RESP_USE_STRLEN);

  vTaskDelay(2000/portTICK_PERIOD_MS);
  esp_restart();
  return ESP_OK;
}

static esp_err_t qr_get_handler(httpd_req_t *req){
  REQUIRE_LOGIN_OR_REDIRECT(req);

  String base;
  if(cfg_host.length()){
    base = "http://" + cfg_host + ".local/";
  } else {
    char hostHdr[64];
    if (httpd_req_get_hdr_value_str(req, "Host", hostHdr, sizeof(hostHdr)) == ESP_OK && strlen(hostHdr) > 0) {
      base = String("http://") + hostHdr + "/";
    } else {
      IPAddress ip = WiFi.isConnected() ? WiFi.localIP() : WiFi.softAPIP();
      base = "http://" + ip.toString() + "/";
    }
  }

  String user = auth_current_user();
  String page = html_qr_page(base, user);

  httpd_resp_set_type(req,"text/html; charset=utf-8");
  return httpd_resp_send(req, page.c_str(), HTTPD_RESP_USE_STRLEN);
}

// ============= Public API =============
static void register_routes(httpd_handle_t server){
  httpd_uri_t wifi_get_uri  = { .uri="/wifi", .method=HTTP_GET,  .handler=wifi_get_handler,  .user_ctx=NULL };
  httpd_uri_t wifi_post_uri = { .uri="/wifi", .method=HTTP_POST, .handler=wifi_post_handler, .user_ctx=NULL };
  httpd_uri_t qr_get_uri    = { .uri="/qr",   .method=HTTP_GET,  .handler=qr_get_handler,    .user_ctx=NULL };
  httpd_register_uri_handler(server, &wifi_get_uri);
  httpd_register_uri_handler(server, &wifi_post_uri);
  httpd_register_uri_handler(server, &qr_get_uri);
}

// goi o setup() truoc start_server()
static void init_and_begin(){
  begin();
}

// helper hien thong tin ra Serial
static void print_status(){
  if(WiFi.getMode()==WIFI_AP){
    Serial.printf("[net] Mode=AP  SSID=%s  IP=%s\n",
      ap_ssid.c_str(), WiFi.softAPIP().toString().c_str());
  }else{
    Serial.printf("[net] Mode=STA SSID=%s  IP=%s\n",
      cfg_ssid.c_str(), WiFi.localIP().toString().c_str());
  }
}

} // namespace wifi_cfg
