# Kumo Cloud API v3

**Note**: This API summary is reverse-engineered and is a work in progress. This is not official documentation from Mistubishi, nor has their permission been sought to publish these findings.

## Summary
The Mitsubishi Kumo Cloud API as used by its mobile apps has changed from a functional but quirky and dated version (with no published version number, as far as I can tell) to a v3 API that uses a more modern approach, with such features as refreshable [JWT](https://en.wikipedia.org/wiki/JSON_Web_Token) authentication and servers capable of using [HTTP/2](https://en.wikipedia.org/wiki/HTTP/2) connections.

The old API is used by PyKumo solely to obtain sufficient information to communicate with indoor units via their (even more quirky) local http interface. As of April 2, 2025 this old API seems to be still working but serving stale data; the new API is what's being updated as customers' information changes.

This document is published in an effort to discover enough of the v3 API to allow pykumo to continue to monitor and control users' indoor units via their local API. It could also serve as a guide for developing a library that monitors and controls units via the cloud.

## Hostname
The scheme and hostname for all endpoints described below is https://app-prod.kumocloud.com/

## Base headers
All or most of the API endpoints seem to require, at a minimum, an `x-app-version` header with a value like `3.0.3`. The base headers that seem to work OK:
```
  Accept: application/json text/plain, */*
  Accept-Encoding: gzip, deflate, br
  Accept-Language: en-US, en
  x-app-version: 3.0.3
```

## Authorization

**Endpoints**
- Login: `/v3/login`
- Refresh: `/v3/refresh`

### Login

The **Login** endpoint is used for initial login, or if the refresh token has expired. POSTing a body as follows (with the base headers) returns a JSON response with access and refresh tokens.
```
{
  "username": "<users-kumo-username>",
  "password": "<users-kumo-password>",
  "appVersion": "3.0.3"
}
```

Example response:

```
{
  "id": "<redacted>",
  "username": "<redacted>",
  "email": "<redacted>",
  "firstName": "<redacted>",
  "lastName": "<redacted>",
  "phone": "<redacted>",
  "isPersonalAccount": true,
  "isEmailVerified": true,
  "isPoliciesAccepted": false,
  "token": {
    "access": "<standard-jwt-token>",
    "refresh": "<standard-jwt-token>"
  },
  "preferences": {
    "sendAnalytics": 1,
    "lastUpdate": <numeric-timestamp>
  },
  "company": null,
  "isSalesforceIntegrated": true
}
```

The access token is short-lived; expiration time about 20 minutes.
The refresh token is long-lived; expiration time about a month.

#### JWT usage
The access token must be provided to all other API requests, in an Authentication header as follows. This is standard JWT usage.
```
  Authentication: Bearer <token-string>
```

### Refresh

If the access token has expired but the refresh token is still valid, a POST to the refresh endpoint with an `Authentication` header (as above) containing the refresh token will provide a response body as follows, bearing new access and refresh tokens. No username or password is required to refresh the tokens. 

POST body:
```
{"refresh": "<refresh-token>"}
```
Response:
```
{
  "access": "<new-access-token>",
  "refresh": <new-refresh-token>"
}
```

Notably, the new refresh token will have a new expiration time one month in the future, and the old refresh token will cease to work.

If the refresh token itself is expired (or not known), the Login endpoint (with username and password) may be used to obtain fresh tokens.

## Account information

**Endpoints**
- Me: `/v3/accounts/me`

A GET returns various account information, quite similar to the response to the initial Login POST.

Details to-be-documented.

## Sites

**Endpoints**
- Collection: `/v3/sites/`
- ?? `/v3/sites/transfers/pending`
- ?? `/v3/notifications/unseen-count`

The **Collection** endpoint returns a list of "sites" associated with the login. Presumably these are separate installations perhaps at different addresses. Example:

`[
  {
    "id": "<guid>",
    "name": "<redacted>",
    "isActive": true,
    "createdAt": "2025-03-30T13:09:59.571Z",
    "updatedAt": "2025-03-30T13:09:59.571Z",
    "schedulesEnabled": true,
    "notificationsEnabled": true,
    "favorite": false,
    "mak": null,
    "baseMAK": null
  }
]
```

The important information here is `id` which is the `{site-id}` for several of the remaining API calls.

The purpose of the 2 remaining site-related endpoints is unknown.

## Per-site

**Endpoints**
- /v3/sites/{site-id}
- /v3/sites/{site-id}/kumo-station
- /v3/sites/{site-id}/zones
- /v3/sites/{site-id}/groups

`{site-id}` is the `id` GUID returned from the `/v3/sites/` collection endpoint.
Description TBD

## Per-zone

**Endpoints**
- /v3/zones/<zone-id>

`{zone-id}` is the `id` GUID returned by the `/v3/{site-id}/zones` endpoint
Description TBD.

## Per-device

**Endpoints**
- /v3/devices/{device-serial}
- /v3/devices/{device-serial}/profile
- /v3/devices/{device-serial}/status
- /v3/devices/{device-serial}/initial-settings
- /v3/devices/{device-serial}/kumo-properties

These endpoints return information per device (indoor unit).

`{device-serial}` is the `adapter.deviceSerial` field returned by the `/v3/sites/{site-id}/zones` endpoint.
These endpoints return operational data for each indoor unit similar to that returned by the local API.

Importantly, the `status` endpoint returns the `cryptoSerial` value, required for local communication with the indoor unit.
