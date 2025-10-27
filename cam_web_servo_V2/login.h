#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include "esp_http_server.h"
#include <mbedtls/md.h>
#include <esp_system.h>

// ====== cấu hình nhanh ======
#define AUTH_ENABLE_CSRF 0   // 0 = tắt CSRF (dễ test), 1 = bật double-submit token
// ===========================

/*
  Login / Session / First-change password
  NVS "auth":
    user : string
    pass : sha256 hex
    must : uint8 (1 = must change on first login)
*/

namespace auth {

/************ STATE ************/
static Preferences pref;
static String s_user;           // stored username
static String s_pass_sha256;    // stored password (SHA-256 hex)
static bool   s_must_change = true;

static String g_session_id;     // RAM-only session token (cookie SID)
// CSRF (chỉ dùng khi AUTH_ENABLE_CSRF=1)
static String g_csrf_pre;       // token trước login
static String g_csrf_session;   // token sau login

/************ UTILS ************/
static inline uint8_t from_hex(char c){
  if (c>='0' && c<='9') return (uint8_t)(c-'0');
  if (c>='a' && c<='f') return (uint8_t)(10 + (c-'a'));
  if (c>='A' && c<='F') return (uint8_t)(10 + (c-'A'));
  return 0;
}

static void url_decode_inplace(char* s){
  char* r = s; char* w = s;
  while (*r) {
    if (*r == '+') { *w++ = ' '; r++; }
    else if (*r == '%' && r[1] && r[2]) {
      uint8_t v = (from_hex(r[1]) << 4) | from_hex(r[2]);
      *w++ = (char)v; r += 3;
    } else {
      *w++ = *r++;
    }
  }
  *w = 0;
}

static void sha256_hex(const char* in, char* out_hex /* >=65 */){
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  unsigned char hash[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  if (mbedtls_md_setup(&ctx, info, 0) == 0) {
    mbedtls_md_starts(&ctx);
    mbedtls_md_update(&ctx, (const unsigned char*)in, strlen(in));
    mbedtls_md_finish(&ctx, hash);
  } else {
    memset(hash, 0, sizeof(hash));
  }
  mbedtls_md_free(&ctx);

  static const char* HEXCHARS = "0123456789abcdef";
  for (int i=0;i<32;i++) {
    out_hex[i*2]   = HEXCHARS[(hash[i]>>4)&0xF];
    out_hex[i*2+1] = HEXCHARS[ hash[i]    &0xF];
  }
  out_hex[64] = 0;
}

static void random_hex32(String& out){
  uint8_t b[16];
  for(int i=0;i<16;i++){ uint32_t r = esp_random(); b[i] = (uint8_t)(r & 0xFF); }
  char hex[33];
  static const char* HEXCHARS = "0123456789abcdef";
  for(int i=0;i<16;i++){
    hex[i*2]   = HEXCHARS[(b[i]>>4)&0xF];
    hex[i*2+1] = HEXCHARS[ b[i]    &0xF];
  }
  hex[32]=0;
  out = hex;
}

static void random_sid(String& sid){ random_hex32(sid); }

/************ FORM PARSER (x-www-form-urlencoded) ************/
static void parse_form_kv(const char* body, const char* key, char* out, size_t outlen){
  if (!body || !key || !out || outlen==0) return;
  size_t klen=strlen(key);
  const char* p=body;
  out[0]=0;
  while(*p){
    if (strncmp(p, key, klen)==0 && p[klen]=='='){
      p += klen+1;
      const char* q = p;
      while(*q && *q!='&') q++;
      size_t len = (size_t)(q-p);
      if (len >= outlen) len = outlen-1;
      memcpy(out, p, len);
      out[len]=0;
      url_decode_inplace(out);
      return;
    }
    while(*p && *p!='&') p++;
    if (*p=='&') p++;
  }
}

/************ COOKIE HELPERS ************/
static bool get_cookie(httpd_req_t* req, const char* name, char* out, size_t outlen){
  size_t needed = httpd_req_get_hdr_value_len(req, "Cookie");
  if (needed == 0) { if(outlen) out[0]=0; return false; }
  char *cookie = (char*)malloc(needed+1);
  if (!cookie) { if(outlen) out[0]=0; return false; }
  httpd_req_get_hdr_value_str(req, "Cookie", cookie, needed+1);

  bool found=false;
  char* p = cookie;
  while (*p) {
    while (*p==' ' || *p==';') p++;
    char* eq = strchr(p, '=');
    if (!eq) break;
    size_t nlen = (size_t)(eq - p);
    char* end = strchr(eq+1, ';');
    if (!end) end = cookie + strlen(cookie);
    if (strlen(name)==nlen && strncmp(p,name,nlen)==0) {
      size_t vlen = (size_t)(end - (eq+1));
      if (vlen >= outlen) vlen = outlen-1;
      memcpy(out, eq+1, vlen);
      out[vlen]=0;
      found=true; break;
    }
    p = end;
  }
  free(cookie);
  if (!found && outlen) out[0]=0;
  return found;
}

/************ NVS LOAD/SAVE ************/
static void load(){
  pref.begin("auth", true);
  s_user        = pref.getString("user", "");
  s_pass_sha256 = pref.getString("pass", "");
  s_must_change = pref.getUChar ("must", 1) != 0;
  pref.end();

  if (s_user.length()==0 || s_pass_sha256.length()==0) {
    char h[65]; sha256_hex("admin", h);
    pref.begin("auth", false);
    pref.putString("user", "admin");
    pref.putString("pass", h);
    pref.putUChar ("must", 1);
    pref.end();
    s_user = "admin";
    s_pass_sha256 = h;
    s_must_change = true;
    Serial.println("[auth] seeded default admin/admin");
  } else {
    Serial.printf("[auth] loaded user='%s', must=%d\n", s_user.c_str(), (int)s_must_change);
  }
}

static void save_creds(const String& user, const String& pass_plain, bool must_change_flag){
  char h[65]; sha256_hex(pass_plain.c_str(), h);
  pref.begin("auth", false);
  pref.putString("user", user);
  pref.putString("pass", h);
  pref.putUChar("must", must_change_flag ? 1 : 0);
  pref.end();
  s_user = user;
  s_pass_sha256 = h;
  s_must_change = must_change_flag;
  Serial.printf("[auth] saved new creds user='%s', must=%d\n", s_user.c_str(), (int)s_must_change);
}

/************ AUTH CORE ************/
static bool password_ok(const String& pass_plain){
  char h[65]; sha256_hex(pass_plain.c_str(), h);
  return (s_pass_sha256.equalsIgnoreCase(h));
}

static bool session_ok(httpd_req_t* req){
  if (g_session_id.length()==0) return false;
  char sid[64];
  if (!get_cookie(req, "SID", sid, sizeof(sid))) return false;
  return (g_session_id.equals(sid));
}

/************ (Optional) CSRF ************/
static void set_cookie(httpd_req_t* req, const char* name, const String& val){
  char buf[200];
  snprintf(buf,sizeof(buf),"%s=%s; HttpOnly; Path=/; SameSite=Lax", name, val.c_str());
  httpd_resp_set_hdr(req,"Set-Cookie",buf);
}

static const char* ensure_prelogin_csrf(httpd_req_t* req){
#if AUTH_ENABLE_CSRF
  if (g_csrf_pre.length()==0) random_hex32(g_csrf_pre);
  set_cookie(req,"CSRF_PRE", g_csrf_pre);
  return g_csrf_pre.c_str();
#else
  return "";
#endif
}

static void rotate_session_csrf(httpd_req_t* req){
#if AUTH_ENABLE_CSRF
  random_hex32(g_csrf_session);
  set_cookie(req,"CSRF", g_csrf_session);
#endif
}

static const char* csrf_token(){
#if AUTH_ENABLE_CSRF
  return g_csrf_session.c_str();
#else
  return "";
#endif
}

static bool csrf_ok_prelogin(httpd_req_t* req, const char* form_token){
#if AUTH_ENABLE_CSRF
  if (!form_token || !*form_token) return false;
  char c[70]; if (!get_cookie(req,"CSRF_PRE",c,sizeof(c))) return false;
  return g_csrf_pre.length()>0 && g_csrf_pre.equals(c) && g_csrf_pre.equals(form_token);
#else
  return true; // bỏ qua khi tắt
#endif
}

static bool csrf_ok_session(httpd_req_t* req, const char* form_token){
#if AUTH_ENABLE_CSRF
  if (!form_token || !*form_token) return false;
  char c[70]; if (!get_cookie(req,"CSRF",c,sizeof(c))) return false;
  return g_csrf_session.length()>0 && g_csrf_session.equals(c) && g_csrf_session.equals(form_token);
#else
  return true; // bỏ qua khi tắt
#endif
}

/*
  Guard route:
  - nếu chưa login → 302 /login
  - nếu must_change → 302 /first-change (trừ một số path)
*/
static bool guard(httpd_req_t* req){
  if (!session_ok(req)) {
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "/login");
    httpd_resp_send(req, "Auth required", HTTPD_RESP_USE_STRLEN);
    return false;
  }
  if (s_must_change) {
    const char* uri = req->uri ? req->uri : "";
    if (strcmp(uri, "/first-change")!=0 && strcmp(uri, "/logout")!=0 && strcmp(uri,"/login")!=0) {
      httpd_resp_set_status(req, "302 Found");
      httpd_resp_set_hdr(req, "Location", "/first-change");
      httpd_resp_send(req, "Change password required", HTTPD_RESP_USE_STRLEN);
      return false;
    }
  }
  return true;
}

} // namespace auth

#define REQUIRE_LOGIN_OR_REDIRECT(req) do { if(!auth::guard(req)) return ESP_OK; } while(0)

namespace auth {

/************ HTML (CSRF field chỉ render khi bật) ************/
static String html_login_with_csrf(const char* token){
  String h =
"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>Login</title>"
"<style>body{font-family:sans-serif;margin:24px}form{max-width:360px;padding:16px;border:1px solid #ddd;border-radius:12px;box-shadow:0 2px 6px rgba(0,0,0,.05)}input,button{width:100%;padding:.6rem;margin:.4rem 0;border-radius:10px;border:1px solid #ccc}button{cursor:pointer}</style>"
"</head><body><h2>Đăng nhập</h2>"
"<form method='POST' action='/login'>";
#if AUTH_ENABLE_CSRF
  h += "<input type='hidden' name='csrf_token' value='"; h += token; h += "'>";
#endif
  h +=
"<label>User</label><input name='user' required>"
"<label>Password</label><input type='password' name='pass' required>"
"<button type='submit'>Login</button>"
"</form>"
"</body></html>";
  return h;
}

static String html_first_with_csrf(const char* token){
  String h =
"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>First-time Change</title>"
"<style>body{font-family:sans-serif;margin:24px}form{max-width:420px;padding:16px;border:1px solid #ddd;border-radius:12px;box-shadow:0 2px 6px rgba(0,0,0,.05)}input,button{width:100%;padding:.6rem;margin:.4rem 0;border-radius:10px;border:1px solid #ccc}button{cursor:pointer}small{color:#555}</style>"
"</head><body><h2>Đổi tài khoản lần đầu</h2>"
"<form method='POST' action='/first-change'>";
#if AUTH_ENABLE_CSRF
  h += "<input type='hidden' name='csrf_token' value='"; h += token; h += "'>";
#endif
  h +=
"<label>User mới</label><input name='newuser' required>"
"<label>Password mới</label><input type='password' name='newpass' required>"
"<label>Nhập lại Password</label><input type='password' name='newpass2' required>"
"<button type='submit'>Lưu</button>"
"<small>Mẹo: đặt mật khẩu tối thiểu 6 ký tự.</small>"
"</form>"
"</body></html>";
  return h;
}

/************ HANDLERS ************/
static esp_err_t login_get(httpd_req_t *req){
  const char* tok = ensure_prelogin_csrf(req);
  String page = html_login_with_csrf(tok);
  httpd_resp_set_type(req,"text/html; charset=utf-8");
  return httpd_resp_send(req, page.c_str(), HTTPD_RESP_USE_STRLEN);
}

static esp_err_t login_post(httpd_req_t *req){
  int total = req->content_len;
  if (total<=0 || total > 2048) return httpd_resp_send_500(req);
  char *buf=(char*)malloc(total+1); if(!buf) return httpd_resp_send_500(req);
  int recvd=0; while(recvd<total){ int r=httpd_req_recv(req,buf+recvd,total-recvd); if(r<=0){ free(buf); return httpd_resp_send_500(req);} recvd+=r; }
  buf[recvd]=0;

  char token[80]="", user[64]="", pass[64]="";
  parse_form_kv(buf,"csrf_token",token,sizeof(token));
  parse_form_kv(buf,"user",user,sizeof(user));
  parse_form_kv(buf,"pass",pass,sizeof(pass));
  bool csrf_ok = csrf_ok_prelogin(req, token);
  Serial.printf("[auth] login_post: csrf_ok=%d user='%s' pass_len=%d\n", (int)csrf_ok, user, (int)strlen(pass));
  free(buf);

  if (!csrf_ok || strlen(user)==0 || strlen(pass)==0) {
    httpd_resp_set_status(req,"302 Found"); httpd_resp_set_hdr(req,"Location","/login");
    return httpd_resp_send(req,"Invalid",7);
  }

  if (s_user.equals(user) && password_ok(pass)) {
    random_sid(g_session_id);
    char setcookie[200];
    snprintf(setcookie,sizeof(setcookie),"SID=%s; HttpOnly; Path=/; SameSite=Lax", g_session_id.c_str());
    httpd_resp_set_hdr(req,"Set-Cookie", setcookie);
    rotate_session_csrf(req); // no-op nếu CSRF tắt

    httpd_resp_set_status(req,"302 Found");
    httpd_resp_set_hdr(req,"Location", s_must_change ? "/first-change" : "/ui");
    return httpd_resp_send(req,"OK",2);
  }

  httpd_resp_set_status(req,"302 Found"); httpd_resp_set_hdr(req,"Location","/login");
  return httpd_resp_send(req,"Wrong",5);
}

static esp_err_t logout_get(httpd_req_t *req){
  g_session_id = "";
  g_csrf_session = "";
  httpd_resp_set_hdr(req,"Set-Cookie","SID=; Max-Age=0; Path=/; SameSite=Lax");
#if AUTH_ENABLE_CSRF
  httpd_resp_set_hdr(req,"Set-Cookie","CSRF=; Max-Age=0; Path=/; SameSite=Lax");
#endif
  httpd_resp_set_status(req,"302 Found");
  httpd_resp_set_hdr(req,"Location","/login");
  return httpd_resp_send(req,"Bye",3);
}

static esp_err_t first_get(httpd_req_t *req){
  if (!session_ok(req)) {
    httpd_resp_set_status(req,"302 Found");
    httpd_resp_set_hdr(req,"Location","/login");
    return httpd_resp_send(req,"Auth",4);
  }
  if (g_csrf_session.length()==0) rotate_session_csrf(req);
  String page = html_first_with_csrf(csrf_token());
  httpd_resp_set_type(req,"text/html; charset=utf-8");
  return httpd_resp_send(req, page.c_str(), HTTPD_RESP_USE_STRLEN);
}

static esp_err_t first_post(httpd_req_t *req){
  if (!session_ok(req)) {
    httpd_resp_set_status(req,"302 Found");
    httpd_resp_set_hdr(req,"Location","/login");
    return httpd_resp_send(req,"Auth",4);
  }
  int total=req->content_len; if(total<=0||total>2048) return httpd_resp_send_500(req);
  char *buf=(char*)malloc(total+1); if(!buf) return httpd_resp_send_500(req);
  int recvd=0; while(recvd<total){ int r=httpd_req_recv(req,buf+recvd,total-recvd); if(r<=0){ free(buf); return httpd_resp_send_500(req);} recvd+=r; }
  buf[recvd]=0;

  char token[80]="", nu[64]="", np1[64]="", np2[64]="";
  parse_form_kv(buf,"csrf_token",token,sizeof(token));
  parse_form_kv(buf,"newuser",nu,sizeof(nu));
  parse_form_kv(buf,"newpass",np1,sizeof(np1));
  parse_form_kv(buf,"newpass2",np2,sizeof(np2));
  bool csrf_ok = csrf_ok_session(req, token);
  free(buf);

  if (!csrf_ok || strlen(nu)==0 || strlen(np1)==0 || strcmp(np1,np2)!=0) {
    httpd_resp_set_status(req,"302 Found"); httpd_resp_set_hdr(req,"Location","/first-change");
    return httpd_resp_send(req,"Invalid",7);
  }

  save_creds(String(nu), String(np1), /*must=*/false);

  httpd_resp_set_status(req,"302 Found");
  httpd_resp_set_hdr(req,"Location","/ui");
  return httpd_resp_send(req,"OK",2);
}

/************ PUBLIC API ************/
static void init(){ load(); }

static void register_routes(httpd_handle_t server){
  httpd_uri_t login_get_uri  = { .uri="/login",        .method=HTTP_GET,  .handler=login_get,   .user_ctx=NULL };
  httpd_uri_t login_post_uri = { .uri="/login",        .method=HTTP_POST, .handler=login_post,  .user_ctx=NULL };
  httpd_uri_t logout_uri     = { .uri="/logout",       .method=HTTP_GET,  .handler=logout_get,  .user_ctx=NULL };
  httpd_uri_t first_get_uri  = { .uri="/first-change", .method=HTTP_GET,  .handler=first_get,   .user_ctx=NULL };
  httpd_uri_t first_post_uri = { .uri="/first-change", .method=HTTP_POST, .handler=first_post,  .user_ctx=NULL };
  httpd_register_uri_handler(server, &login_get_uri);
  httpd_register_uri_handler(server, &login_post_uri);
  httpd_register_uri_handler(server, &logout_uri);
  httpd_register_uri_handler(server, &first_get_uri);
  httpd_register_uri_handler(server, &first_post_uri);
}

} // namespace auth

/************ SHIMs cho file khác ************/
extern "C" const char* auth_current_user() {
  static String cache;
  Preferences p;
  p.begin("auth", /*readOnly*/ true);
  cache = p.getString("user", "admin");
  p.end();
  return cache.c_str();
}

// Lấy CSRF token phiên (để chèn vào form /wifi) — sẽ là chuỗi rỗng nếu CSRF tắt
extern "C" const char* auth_csrf_token() {
  return auth::csrf_token();
}
