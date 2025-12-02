# Dominion Energy Authentication Flow Documentation

## Overview

Dominion Energy uses SAP Customer Data Cloud (Gigya) for authentication. The login flow involves:
1. SDK Bootstrap (cookie initialization)
2. Credential submission (triggers TFA)
3. Two-Factor Authentication via SMS or Email
4. Session finalization

**Base URL:** `https://auth.dominionenergy.com`
**API Key:** `4_6zEg-HY_0eqpgdSONYkJkQ`

---

## Authentication Flow

### Step 0: Load Login Page (WAF Cookies)

First, load the login page to obtain Incapsula WAF cookies. These are required for all subsequent API calls.

```
GET https://login.dominionenergy.com/CommonLogin?SelectedAppName=Electric
```

**Headers:**
| Header | Value |
|--------|-------|
| Accept | `text/html,application/xhtml+xml,application/xml;q=0.9,...` |
| Referer | `https://myaccount.dominionenergy.com/` |
| User-Agent | Browser user agent |

**Response:** HTML page (content not needed)

**Cookies Set:**
- `incap_ses_*` - Incapsula session cookie
- `nlbi_*` - Incapsula load balancer cookie
- `visid_incap_*` - Incapsula visitor ID (long-lived)
- `SelectedAppName` - Set to `electric`

---

### Step 1: Bootstrap SDK (Gigya Cookies)

Initialize the Gigya SDK session. Requires WAF cookies from Step 0.

```
GET https://auth.dominionenergy.com/accounts.webSdkBootstrap
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| apiKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| pageURL | `https://login.dominionenergy.com/CommonLogin?SelectedAppName=Electric` |
| sdk | `js_latest` |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "apiVersion": 2,
  "statusCode": 200,
  "statusReason": "OK",
  "time": "2025-01-01T12:00:00.000Z",
  "hasGmid": "ver4"
}
```

**Cookies Set:**
- `gmid` - Gigya machine ID (long-lived)
- `ucid` - User context ID (long-lived)
- `hasGmid` - Gmid version flag
- `gig_bootstrap_*` - Bootstrap marker

---

### Step 2: Login with Credentials

Submit username/password. Returns TFA pending status.

```
POST /accounts.login
Content-Type: application/x-www-form-urlencoded
```

**Form Parameters:**
| Parameter | Value |
|-----------|-------|
| loginID | User email |
| password | User password |
| sessionExpiration | `31556952` (1 year in seconds) |
| targetEnv | `jssdk` |
| include | `profile,data,emails,subscriptions,preferences,id_token,groups,loginIDs,` |
| includeUserInfo | `true` |
| captchaToken | `0` |
| captchaType | `reCaptchaEnterpriseScore` |
| loginMode | `standard` |
| lang | `en` |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| source | `showScreenSet` |
| sdk | `js_latest` |
| authMode | `cookie` |
| pageURL | `https://login.dominionenergy.com/CommonLogin?SelectedAppName=Electric` |
| sdkBuild | `18106` |
| format | `json` |

**Response (TFA Required):**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 403101,
  "errorDetails": "Pending Two-Factor Authentication",
  "errorMessage": "Account Pending TFA Verification",
  "apiVersion": 2,
  "statusCode": 403,
  "statusReason": "Forbidden",
  "time": "2025-01-01T12:00:00.000Z",
  "UID": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "id_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiI...",
  "regToken": "st2.s.XXXXXXXXXX.YYYYYYYYYYYYYYYYYYYY...",
  "sessionInfo": {
    "expires_in": "3600"
  }
}
```

**Key Values:**
- `regToken` - Required for all subsequent TFA calls
- `UID` - User identifier
- `id_token` - JWT with user claims (isLoggedIn: false at this stage)

---

### Step 3: Get TFA Providers

Retrieve available TFA methods for the user.

```
GET /accounts.tfa.getProviders
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| regToken | From Step 2 response |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "apiVersion": 2,
  "statusCode": 200,
  "statusReason": "OK",
  "time": "2025-01-01T12:00:00.000Z",
  "activeProviders": [
    { "name": "gigyaEmail" },
    { "name": "gigyaPhone" }
  ],
  "inactiveProviders": [],
  "pendingOptin": []
}
```

---

### Step 4: Initialize TFA

Initialize the chosen TFA provider to get a `gigyaAssertion` JWT.

```
GET /accounts.tfa.initTFA
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| provider | `gigyaPhone` or `gigyaEmail` |
| mode | `verify` |
| regToken | From Step 2 |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "apiVersion": 2,
  "statusCode": 200,
  "statusReason": "OK",
  "time": "2025-01-01T12:00:00.000Z",
  "gigyaAssertion": "eyJhbGciOiJSUzI1NiIsImtpZCI6IkRENDFE..."
}
```

**Important:** The `gigyaAssertion` JWT has a short expiry (5 minutes). It must be used promptly.

---

### Step 5a: Get Registered Phone Numbers (Phone TFA)

```
GET /accounts.tfa.phone.getRegisteredPhoneNumbers
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| gigyaAssertion | From Step 4 |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "phones": [
    {
      "id": "XXXXXXXXXXXXXXXXXXXXXX",
      "obfuscated": "+########XXX",
      "lastMethod": "sms",
      "lastVerification": "2025-01-01T12:00:00.000Z"
    }
  ],
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "statusCode": 200
}
```

---

### Step 5b: Get Registered Emails (Email TFA)

```
GET /accounts.tfa.email.getEmails
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| gigyaAssertion | From Step 4 (with provider=gigyaEmail) |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "emails": [
    {
      "id": "XXXXXXXXXXXXXXXXXXXXXX",
      "obfuscated": "us***er@example.com",
      "lastVerification": "2025-01-01T12:00:00.000Z"
    }
  ],
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "statusCode": 200
}
```

---

### Step 6: Send Verification Code (Phone)

Request SMS code to be sent.

```
GET /accounts.tfa.phone.sendVerificationCode
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| gigyaAssertion | From Step 4 (fresh - may need to re-init) |
| lang | `en` |
| phoneID | Phone ID from Step 5a |
| method | `sms` or `voice` |
| regToken | From Step 2 |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Response:**
```json
{
  "phvToken": "MhF~XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX~YYYYYYYYYYYY...",
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "apiVersion": 2,
  "statusCode": 200,
  "statusReason": "OK",
  "time": "2025-01-01T12:00:00.000Z"
}
```

**Key Value:**
- `phvToken` - Phone verification token, required for code submission

---

### Step 7: Complete Phone Verification

Submit the OTP code received via SMS.

```
GET /accounts.tfa.phone.completeVerification
```

**Query Parameters:**
| Parameter | Value |
|-----------|-------|
| gigyaAssertion | From Step 4 (same one used in Step 6) |
| phvToken | From Step 6 |
| code | The 6-digit OTP code |
| regToken | From Step 2 |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| pageURL | Login page URL |
| sdkBuild | `18148` |
| format | `json` |

**Success Response:**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "apiVersion": 2,
  "statusCode": 200,
  "statusReason": "OK",
  "providerAssertion": "eyJ..."
}
```

**Error Response (Invalid JWT):**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 400006,
  "errorDetails": "Invalid jwt",
  "errorMessage": "Invalid parameter value",
  "apiVersion": 2,
  "statusCode": 400,
  "statusReason": "Bad Request"
}
```

**Note:** The same `gigyaAssertion` used for `sendVerificationCode` must be used for `completeVerification`. JWT expiration or token mismatch will cause "Invalid jwt" errors.

---

### Step 8: Finalize TFA / Complete Login

After successful phone verification, call `accounts.tfa.finalizeTFA` which returns the same response format as a successful login (Step 2 without TFA).

**Success Response (same as login without TFA):**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "statusCode": 200,
  "UID": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "UIDSignature": "XXXXXXXXXXXXXXXXXXXXXXXXX=",
  "signatureTimestamp": "1700000000",
  "profile": {
    "firstName": "John",
    "lastName": "Doe",
    "email": "user@example.com"
  },
  "id_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiI...",
  "sessionInfo": {
    "login_token": "st2.s.XXXXXXXXXX.YYYYYYYYYYYYYYYYYYYY...",
    "expires_in": "3600"
  }
}
```

**Key Values:**
- `id_token` - JWT with `isLoggedIn: true`
- `sessionInfo.login_token` - Required for getAccountInfo (expires in 1 hour)

---

### Step 9: Get Account Info

Exchange the login_token for a fresh id_token.

```
POST /accounts.getAccountInfo
Content-Type: application/x-www-form-urlencoded
```

**Cookies Required:**
- All previous cookies PLUS
- `glt_4_6zEg-HY_0eqpgdSONYkJkQ` = `{login_token}` (login token as cookie)

**Form Parameters:**
| Parameter | Value |
|-----------|-------|
| include | `groups,profile,data,id_token,` |
| lang | `en` |
| APIKey | `4_6zEg-HY_0eqpgdSONYkJkQ` |
| sdk | `js_latest` |
| login_token | From Step 8 sessionInfo |
| authMode | `cookie` |
| pageURL | Login page URL |
| sdkBuild | `18106` |
| format | `json` |

**Response:**
```json
{
  "callId": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "errorCode": 0,
  "statusCode": 200,
  "UID": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "profile": {
    "firstName": "John",
    "lastName": "Doe",
    "email": "user@example.com"
  },
  "data": {
    "entitlements": {
      "Electric": { "EMYA": true, "ELL": false, "ECI": false, "EAG": false }
    },
    "MyAccountRegistration": true,
    "legacyUID": "XXXXXXXXXXXXXXXXXXX"
  },
  "id_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiI..."
}
```

**Key Value:**
- `id_token` - Fresh JWT to use for Dominion API auth

---

### Step 10: Exchange for Dominion API Tokens

Use the Gigya `id_token` to get Dominion API access/refresh tokens.

**Base URL:** `https://prodsvc-dominioncip.smartcmobile.com`

```
POST /UsermanagementAPI/api/1/Login/auth
Content-Type: application/json
Authorization: Bearer {id_token}
```

**Headers:**
| Header | Value |
|--------|-------|
| Authorization | `Bearer {id_token}` |
| Content-Type | `application/json` |
| e2eid | UUID (e.g., `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) |
| pt | (empty) |
| st | `PL` |
| uid | `1` |
| Origin | `https://myaccount.dominionenergy.com` |

**Request Body:**
```json
{
  "username": "",
  "password": "",
  "guestToken": "{id_token}",
  "customattributes": {
    "client": "",
    "version": "",
    "deviceId": "",
    "deviceName": "",
    "os": ""
  }
}
```

**Response:**
```json
{
  "status": {
    "type": "success",
    "code": 200,
    "message": "success",
    "error": false
  },
  "data": {
    "accessToken": "eyJhbGciOiJodHRwOi8vd3d3LnczLm9yZy8yMDAxLzA0L3htbGRzaWctbW9yZSNobWFjLXNoYTI1NiI...",
    "tokenType": "Bearer",
    "refreshToken": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX=",
    "expiresIn": 30,
    "user": {
      "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "firstName": "John",
      "lastName": "Doe",
      "email": "user@example.com",
      "status": "Active"
    }
  }
}
```

**Key Values:**
- `accessToken` - JWT for all Dominion API calls (30 min expiry)
- `refreshToken` - For token refresh via existing refresh endpoint
- `expiresIn` - 30 minutes

---

## Complete Flow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    DOMINION ENERGY LOGIN FLOW                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  0. Load Login Page ────────────────────────────> WAF Cookies    │
│     GET /CommonLogin?SelectedAppName=Electric     (incap, nlbi,  │
│                                                    visid)        │
│                                                                  │
│  1. Bootstrap SDK ──────────────────────────────> Gigya Cookies  │
│     GET /accounts.webSdkBootstrap                 (gmid, ucid)   │
│                                                                  │
│  2. Login ──────────────────────────────────────> regToken       │
│     POST /accounts.login                          OR             │
│                                                   id_token +     │
│                                                   login_token    │
│                         ┌───────────────────┐                    │
│                         │  TFA Required?    │                    │
│                         └─────────┬─────────┘                    │
│                    No ────────────┴────────── Yes                │
│                    │                          │                  │
│                    │    3. Get TFA Providers  │                  │
│                    │    4. Init TFA           │                  │
│                    │    5. Get Phones/Emails  │                  │
│                    │    6. Send Code          │                  │
│                    │    7. Verify Code ───────┘                  │
│                    │              │                              │
│                    │              ▼                              │
│                    │    8. Finalize TFA ──────> id_token +       │
│                    │                            login_token      │
│                    │              │                              │
│                    ▼              ▼                              │
│  9. Get Account Info ───────────────────────────> Fresh id_token │
│     POST /accounts.getAccountInfo                                │
│                                                                  │
│  10. Dominion API Auth ─────────────────────────> accessToken +  │
│      POST /Login/auth                              refreshToken  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Critical Implementation Notes

### 1. Cookie Management (Two-Stage)
Cookies are obtained in two stages and must be maintained throughout the flow:

**Stage 1 - WAF Cookies (Step 0):** Load login page first
- `incap_ses_*` - Session cookie
- `nlbi_*` - Load balancer cookie
- `visid_incap_*` - Visitor ID (long-lived)

**Stage 2 - Gigya Cookies (Step 1):** SDK bootstrap
- `gmid` - Machine ID (long-lived)
- `ucid` - User context ID (long-lived)
- `hasGmid` - Version flag
- `gig_bootstrap_*` - Bootstrap marker

All subsequent API calls require cookies from both stages.

### 2. JWT Expiration
The `gigyaAssertion` JWT expires in ~5 minutes. The flow must be:
1. Call `initTFA` to get fresh assertion
2. Immediately use it for `sendVerificationCode`
3. Keep the SAME assertion for `completeVerification`

### 3. Token Continuity
The `regToken` from Step 2 is used throughout the entire TFA flow.

### 4. Common Errors

| Error Code | Message | Cause |
|------------|---------|-------|
| 403101 | Account Pending TFA Verification | TFA required - proceed with TFA flow |
| 400006 | Invalid jwt | Expired or mismatched gigyaAssertion |
| 403042 | Invalid LoginID | Email not found |
| 403043 | Invalid Login or Password | Wrong password |

---

## Token Lifecycle Summary

| Token | Source | Expiry | Storage |
|-------|--------|--------|---------|
| `gmid` cookie | Bootstrap | Session | CookieJar |
| `regToken` | Login (TFA pending) | ~10 min | Memory only |
| `gigyaAssertion` | initTFA | 5 min | Memory only |
| `phvToken` | sendVerificationCode | ~5 min | Memory only |
| `login_token` | Login/Finalize TFA | 1 hour | Memory only |
| `id_token` (Gigya) | getAccountInfo | ~1 min | Memory only |
| `accessToken` (Dominion) | /Login/auth | 30 min | tokens.json |
| `refreshToken` (Dominion) | /Login/auth | ~days | tokens.json |
