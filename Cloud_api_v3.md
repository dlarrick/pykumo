# Kumo Cloud API v3

**Note**: This API summary is reverse-engineered and is a work in progress. This is not official documentation from Mistubishi, nor has their permission been sought to publish these findings.

## Summary
The Mitsubishi Kumo Cloud API as used by its mobile apps has changed from a functional but quirky and dated version (with no published version number, as far as I can tell) to a v3 API that uses a more modern approach, with such features as refreshable [JWT](https://en.wikipedia.org/wiki/JSON_Web_Token) authentication and servers capable of using [HTTP/2](https://en.wikipedia.org/wiki/HTTP/2) connections.

The old API is used by PyKumo solely to obtain sufficient information to communicate with indoor units via their (even more quirky) local http interface. As of April 2, 2025 this old API seems to be still working but serving stale data; the new API is what's being updated as customers' information changes.

This document is published in an effort to discover enough of the v3 API to allow pykumo to continue to monitor and control users' indoor units via their local API. It could also serve as a guide for developing a library that monitors and controls units via the cloud.

### Remaining to be documented

Portions of the v3 API are not yet documented:
- PATCH endpoint structure

## WebSocket interface
More information -- including the indoor unit password -- is available via a WebSocket interface. [HA-Kumo-WS](https://github.com/EnumC/ha_kumo_ws) is an entirely cloud-based integration and has examples of using this WebSocket. 

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

### Collection Endpoint
The **Collection** endpoint returns a list of "sites" associated with the login. Presumably these are separate installations perhaps at different addresses. These are called _Locations_ in the Comfort app.

Example:

```
[
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

### Pending Transfers
In the app, a Location can be transferred to a new owner. This is done by email address, and this endpoint allows the app to notify a user about an incoming transfer request.

### Unseen Notifications
This populates the Red Dot on the Notification Bell in the Comfort app.

## Site endpoints

**Endpoints**
- /v3/sites/{site-id}
- /v3/sites/{site-id}/kumo-station
- /v3/sites/{site-id}/zones
- /v3/sites/{site-id}/groups

`{site-id}` is the `id` GUID returned from the `/v3/sites/` collection endpoint.

### sites/{site-id}
```
{
    "id": "<site-id>",
    "name": "<redacted>",
    "isActive": true,
    "createdAt": "2025-03-25T19:19:27.828Z",
    "updatedAt": "2025-04-08T19:10:33.928Z",
    "address": "<redacted>",
    "address2": "<redacted>",
    "city": "<redacted>",
    "state": "<redacted>",
    "zip": "<redacted>",
    "country": "<redacted>",
    "favorite": false,
    "schedulesEnabled": true,
    "notificationsEnabled": true,
    "requiresAddressUpdate": false,
    "mak": "<redacted?>",
    "baseMAK": null
}
```

### sites/{site-id}/groups

**minRuntime**: the minimum time the system should run in heating or cooling mode before switching.
Available values in the app: 10, 20, 30, 40 minutes

**maxStandby**: the maximum time your system should wait in heating or cooling mode before switching.
Available values in the app: 30 minutes, 1, 2, 3, 4 hours
```
[
    {
        "id": "<group-id>",
        "name": "<redacted>",
        "isActive": true,
        "createdAt": "2025-04-08T16:21:44.662Z",
        "updatedAt": "2025-04-08T16:25:01.306Z",
        "systemChangeoverEnabled": true,
        "minRuntime": 30,
        "maxStandby": 60
    }
]
```

### sites/{site-id}/zones
```
[
    {
        "id": "<zone-id>",
        "name": "First Floor",
        "isActive": true,
        "group": {
            "id": "<group-id>",
            "name": "<redacted>",
            "isActive": true,
            "createdAt": "2025-04-08T16:21:44.662Z",
            "updatedAt": "2025-04-08T16:25:01.306Z",
            "systemChangeoverEnabled": true,
            "minRuntime": 30,
            "maxStandby": 60
        },
        "adapter": {
            "id": "<adapter-id>",
            "deviceSerial": "<adapter-serial>",
            "isSimulator": false,
            "roomTemp": 22,
            "spCool": 23,
            "spHeat": 21.5,
            "spAuto": null,
            "humidity": 41,
            "scheduleOwner": "adapter",
            "power": 1,
            "operationMode": "autoHeat",
            "connected": true,
            "hasSensor": false,
            "hasMhk2": true,
            "timeZone": "America/Los_Angeles",
            "isHeadless": false,
            "lastStatusChangeAt": "2025-04-05T17:41:41.644Z",
            "createdAt": "2025-03-29T00:20:20.730Z",
            "updatedAt": "2025-04-09T20:01:46.080Z"
        },
        "createdAt": "2025-03-29T00:20:20.735Z",
        "updatedAt": "2025-04-08T17:02:25.675Z"
    },
    {
        "id": "<zone-id>",
        "name": "<redacted>",
        "isActive": true,
        "group": {
            "id": "<group-id>",
            "name": "<redacted>",
            "isActive": true,
            "createdAt": "2025-04-08T16:21:44.662Z",
            "updatedAt": "2025-04-08T16:25:01.306Z",
            "systemChangeoverEnabled": true,
            "minRuntime": 30,
            "maxStandby": 60
        },
        "adapter": {
            "id": "<adapter-id>",
            "deviceSerial": "<adapter-serial>",
            "isSimulator": false,
            "roomTemp": 21,
            "spCool": 23.5,
            "spHeat": 21.5,
            "spAuto": null,
            "humidity": null,
            "scheduleOwner": "adapter",
            "power": 1,
            "operationMode": "autoHeat",
            "connected": true,
            "hasSensor": false,
            "hasMhk2": false,
            "timeZone": "America/Los_Angeles",
            "isHeadless": false,
            "lastStatusChangeAt": "2025-04-08T16:20:16.450Z",
            "createdAt": "2025-04-08T16:20:15.982Z",
            "updatedAt": "2025-04-09T18:07:17.220Z"
        },
        "createdAt": "2025-04-08T16:20:15.988Z",
        "updatedAt": "2025-04-08T17:03:27.214Z"
    }
]
```

### sites/{site-id}/kumo-station
Description TBD. (I get `"error": "kumoStationNotFound"`)

## Group endpoints

**Endpoints**
- /v3/groups/{group-id}

`{group-id}` is the `id` GUID returned from the `/v3/sites/{site-id}/groups` endpoint.

### /v3/groups/{group-id}
```
{
    "id": "<group-id>",
    "name": "<redacted>",
    "isActive": true,
    "createdAt": "2025-04-08T16:21:44.662Z",
    "updatedAt": "2025-04-08T16:25:01.306Z",
    "masterZone": {
        "id": "<zone-id>",
        "name": "<redacted>"
    },
    "systemChangeoverEnabled": true,
    "minRuntime": 30,
    "maxStandby": 30,
    "zones": [
        {
            "id": "<zone-id>",
            "name": "<redacted>",
            "isActive": true,
            "createdAt": "2025-03-29T00:20:20.735Z",
            "updatedAt": "2025-04-08T17:02:25.675Z",
            "isChangeoverPriority": true,
            "changeoverPriority": 1
        },
        {
            "id": "<zone-id>",
            "name": "<redacted>",
            "isActive": true,
            "createdAt": "2025-04-08T16:20:15.988Z",
            "updatedAt": "2025-04-08T17:03:27.214Z",
            "isChangeoverPriority": true,
            "changeoverPriority": 2
        }
    ]
}
```

## Zone endpoints

**Endpoints**
- /v3/zones/{zone-id}

`{zone-id}` is the `id` GUID returned by the `/v3/sites/{site-id}/zones` endpoint

### zones/{zone-id}
```
{
    "id": "<zone-id>",
    "name": "<redacted>",
    "isActive": true,
    "group": {
        "id": "<group-id>",
        "name": "<redacted>",
        "isActive": true,
        "createdAt": "2025-04-08T16:21:44.662Z",
        "updatedAt": "2025-04-09T20:22:09.035Z",
        "systemChangeoverEnabled": true,
        "minRuntime": 30,
        "maxStandby": 60
    },
    "adapter": {
        "id": "<adapter-id>",
        "deviceSerial": "<device-serial>",
        "isSimulator": false,
        "roomTemp": 22,
        "spCool": 23,
        "spHeat": 21.5,
        "spAuto": null,
        "humidity": 43,
        "scheduleOwner": "adapter",
        "power": 1,
        "operationMode": "autoHeat",
        "connected": true,
        "hasSensor": false,
        "hasMhk2": true,
        "timeZone": "America/Los_Angeles",
        "isHeadless": false,
        "lastStatusChangeAt": "2025-04-05T17:41:41.644Z",
        "createdAt": "2025-03-29T00:20:20.730Z",
        "updatedAt": "2025-04-09T20:27:05.796Z"
    },
    "createdAt": "2025-03-29T00:20:20.735Z",
    "updatedAt": "2025-04-08T17:02:25.675Z"
}
```

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

### devices/{device-serial}
```
{
    "id": "<adapter-id>",
    "deviceSerial": "<device-serial>",
    "rssi": -42,
    "power": 1,
    "operationMode": "autoHeat",
    "humidity": 43,
    "scheduleOwner": "adapter",
    "fanSpeed": "auto",
    "airDirection": "vertical",
    "roomTemp": 22,
    "unusualFigures": 32768,
    "twoFiguresCode": "A0",
    "statusDisplay": 0,
    "spCool": 23,
    "spHeat": 21.5,
    "spAuto": null,
    "runTest": 0,
    "activeThermistor": null,
    "tempSource": null,
    "isSimulator": false,
    "serialNumber": "<redacted>",
    "modelNumber": "SVZ-KP30NA",
    "ledDisabled": false,
    "connected": true,
    "isHeadless": false,
    "lastStatusChangeAt": "2025-04-05T17:41:41.644Z",
    "createdAt": "2025-03-29T00:20:20.730Z",
    "updatedAt": "2025-04-09T20:37:15.239Z",
    "model": {
        "id": "67b6aba8-bc5b-4c92-9ea9-14a5320747c8",
        "brand": "Mitsubishi",
        "material": "SVZ-KP30NA",
        "basicMaterial": "SVZ-KP30NA",
        "replacementMaterial": "SVZ-AP30NL",
        "materialDescription": "MULTI POSITION INDOOR",
        "family": "SVZ",
        "subFamily": "SVZ",
        "materialGroupName": "PAC indoor",
        "serialProfile": "ZEA",
        "materialGroupSeries": "P-Series",
        "isIndoorUnit": true,
        "isDuctless": null,
        "isSwing": null,
        "isPowerfulMode": null,
        "modeDescription": "INDOOR UNIT",
        "isActive": true,
        "frontendAnimation": "ducted",
        "gallery": {
            "id": "fb9153ad-b3ac-4065-bbef-66ceeae809b6",
            "name": "Air handler",
            "imageUrl": "https://dw2p0k56b2hr9.cloudfront.net/small_ME_PVA_Air_Handler_Front_copy_9135d8f255.webp",
            "imageAlt": "Air handler"
        },
        "createdAt": "2025-03-12T19:31:06.038Z",
        "updatedAt": "2025-03-12T19:31:06.038Z"
    },
    "displayConfig": {
        "filter": false,
        "defrost": false,
        "hotAdjust": false,
        "standby": false
    },
    "timeZone": "America/Los_Angeles"
}
```

### devices/{device-serial}/profile
```
[
    {
        "hasModeDry": true,
        "hasModeHeat": true,
        "hasVaneDir": false,
        "hasVaneSwing": false,
        "hasModeVent": true,
        "hasFanSpeedAuto": true,
        "hasInitialSettings": true,
        "hasModeTest": true,
        "numberOfFanSpeeds": 3,
        "extendedTemps": true,
        "usesSetPointInDryMode": true,
        "hasHotAdjust": true,
        "hasDefrost": true,
        "hasStandby": true,
        "maximumSetPoints": {
            "cool": 30,
            "heat": 28,
            "auto": 28
        },
        "minimumSetPoints": {
            "cool": 19,
            "heat": 10,
            "auto": 19
        }
    }
]
```

### devices/{device-serial}/status
```
{
    "autoModeDisable": true,
    "firmwareVersion": "02.06.12",
    "roomTempDisplayOffset": 0,
    "routerSsid": "<redacted>",
    "routerRssi": -42,
    "optimalStart": null,
    "minSetPoint": 16,
    "maxSetPoint": 31,
    "modeHeat": true,
    "modeDry": false,
    "receiverRelay": "MHK2",
    "lastUpdated": "2025-04-09T19:32:36.433Z",
    "cryptoSerial": "<cryptoSerial>",
    "cryptoKeySet": "F"
}
```

### devices/{device-serial}/initial-settings
```
[
    {
        "deviceSerial": "<redacted>",
        "settingNumber": 1,
        "settingValue": 2
    },
    {
        "deviceSerial": "<redacted>",
        "settingNumber": 2,
        "settingValue": 1
    },
    ...
]
```
### devices/{device-serial}/kumo-properties
```
{
    "deviceSerial": "<device-serial>",
    "reporting": {},
    "heatModeDisable": false,
    "connected": false,
    "outdoorAirTemperature": null,
    "sourceReport": null,
    "lastUpdated": "2025-04-09T20:24:45.093Z"
}
```
