# Meta Marketing API — Ad Fields Reference

How our engine maps to Meta's Campaign → AdSet → Ad → AdCreative hierarchy.
API version: v25.0. Source: [Meta Marketing API docs](https://developers.facebook.com/docs/marketing-api/).

---

## Object Hierarchy

```
Ad Account (act_XXXXXXXXXX)
└── Campaign          — objective, budget type
    └── Ad Set         — audience, schedule, bid strategy, daily budget
        └── Ad         — name, creative reference, status
            └── AdCreative  — the actual visual + copy payload
```

We do **not** create Campaigns or Ad Sets programmatically — those are managed manually in Ads Manager. The engine only creates AdCreatives and Ads, and attaches them to an existing Ad Set chosen at deploy time.

---

## AdCreative Fields

**Endpoint:** `POST /act_{account_id}/adcreatives`

### Top-level fields

| Field | Required | Limit | Description |
|---|---|---|---|
| `name` | Yes | 100 chars | Internal library name for this creative. Non-empty. |
| `object_story_spec` | Yes | — | The inline ad content — see below. Do not combine with `asset_feed_spec`. |
| `url_tags` | No | — | UTM params appended to all click URLs. Format: `utm_source=facebook&utm_medium=paid&utm_campaign=...` |
| `degrees_of_freedom_spec` | No | — | Opt-in to Meta's Advantage+ Creative (AI variations). Omit unless explicitly wanted. |
| `authorization_category` | No | — | Required only for political/issue ads. Set to `POLITICAL` if applicable; otherwise omit. |

### `object_story_spec`

| Field | Required | Description |
|---|---|---|
| `page_id` | Yes | Facebook Page ID. The page must be accessible by the access token. |
| `link_data` | Yes (for image/link ads) | The ad content — see below. |

### `link_data` (single-image feed ad)

| Field | Required | Limit | Description |
|---|---|---|---|
| `image_hash` | Yes | — | Hash returned by AdImage upload. Do **not** send alongside `picture`. |
| `link` | Yes | — | Destination URL. Must be a valid URL. Must match `call_to_action.value.link`. |
| `message` | Yes (in practice) | 125 chars recommended; 2,200 hard limit | Primary text shown above the image. Omitting causes "Ad Incomplete" for feed placements. |
| `name` | No | 25 chars recommended | Headline / link title shown below the image. Omit rather than send empty string. |
| `description` | No | 30 chars recommended; 255 hard | Link description beneath the headline. **Omit entirely if empty** — sending `""` fails validation. |
| `call_to_action` | No (but required in practice) | — | CTA button. See format below. Omit rather than send `{"type": "NO_BUTTON"}`. |

> **Empty string rule:** Never send `""` for optional text fields (`description`, `name`, `caption`). The API rejects them. Omit the key entirely when there is no value.

### `call_to_action` format

```json
{
  "type": "LEARN_MORE",
  "value": {
    "link": "https://jotpsych.com"
  }
}
```

`call_to_action.value.link` is **required** and must match `link_data.link` exactly. Omitting `value` triggers error code 100 / subcode 2446391 ("Ad Incomplete").

#### Valid `type` values for link ads

| Type | Use Case |
|---|---|
| `LEARN_MORE` | Generic — always safe default |
| `SIGN_UP` | Account creation / trial |
| `GET_STARTED` | Onboarding / product intro |
| `CONTACT_US` | Lead gen / demo request |
| `BOOK_NOW` | Scheduling |
| `GET_QUOTE` | Pricing inquiry |
| `APPLY_NOW` | Application flow |
| `DOWNLOAD` | Asset / app download |
| `SHOP_NOW` | E-commerce |
| `SUBSCRIBE` | Newsletter / membership |
| `WATCH_VIDEO` | Video content |
| `NO_BUTTON` | No CTA rendered |

Our `CTA_MAP` in `deployer.py` maps plain-English button text to these type strings.

---

## Ad Fields

**Endpoint:** `POST /act_{account_id}/ads`

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Internal name for this ad. We use the headline (truncated to 100 chars). |
| `adset_id` | Yes | ID of the existing Ad Set to attach this ad to. |
| `creative` | Yes | Object: `{"creative_id": "<id>"}` — references the AdCreative just created. |
| `status` | Yes | `PAUSED` or `ACTIVE`. We create as `PAUSED` — activate manually in Ads Manager after verifying the creative. |

---

## AdImage Upload

**Endpoint:** `POST /act_{account_id}/adimages`

Upload the image file first; the returned `hash` is used as `link_data.image_hash`.

```python
image = AdImage(parent_id=ad_account_id)
image[AdImage.Field.filename] = "/path/to/image.png"
image.remote_create()
image_hash = image[AdImage.Field.hash]
```

- Accepted formats: JPG, PNG
- Recommended size: at least 1080px on the shortest side
- Max file size: 30 MB
- The hash is tied to the ad account — cannot be shared across accounts

---

## Ad Review Status

After an ad is set to `ACTIVE`, Meta queues it for review. Poll `effective_status` via:

```
GET /{ad_id}?fields=effective_status,review_feedback
```

| `effective_status` | Meaning |
|---|---|
| `PENDING_REVIEW` | In Meta's review queue (typically 24–48h) |
| `ACTIVE` | Approved and serving |
| `DISAPPROVED` | Rejected — see `review_feedback[].rejection_reasons` |
| `PAUSED` | Not in review; manually paused |
| `WITH_ISSUES` | Running but flagged — check `review_feedback` |

---

## Character Limits Summary

| Field | Recommended Max | Hard Limit |
|---|---|---|
| Primary text (`message`) | 125 chars | 2,200 chars |
| Headline (`name` in link_data) | 25 chars | ~255 chars (truncates in display) |
| Description | 30 chars | 255 chars |
| Creative `name` (library) | 100 chars | 100 chars |
| Ad `name` (library) | 100 chars | 100 chars |

### Text content rules

- Cannot start with punctuation
- No consecutive punctuation (except `...`)
- No special characters: `★ ^ ~ _ = { } [ ] | < >`
- Individual words must be under 30 characters
- No IPA symbols, diacritical marks, superscript/subscript (™ and ℠ are OK)
- No excessive capitalization

---

## Common Errors

| Code | Subcode | Message | Likely Cause |
|---|---|---|---|
| 100 | 2446391 | Ad Incomplete | Missing `call_to_action.value.link`, or an optional field sent as `""` |
| 200 | — | Ads_management permission not granted | System user not assigned to the ad account in Business Manager |
| 100 | — | Invalid parameter | Malformed JSON, unknown field name, or type mismatch |
| 190 | — | Invalid OAuth access token | Token expired or revoked |
| 613 | — | Calls to this api have exceeded the rate limit | Back off and retry with exponential backoff |

---

## What Our Engine Sends (Current Payload)

```python
# AdCreative
{
    "name": variant.headline[:100],
    "object_story_spec": {
        "page_id": META_PAGE_ID,
        "link_data": {
            "image_hash": "<uploaded_image_hash>",
            "link": destination_url,
            "message": variant.primary_text,
            "name": variant.headline,
            # description omitted if empty
            "description": variant.description,   # only if non-empty
            "call_to_action": {
                "type": CTA_MAP[variant.cta_button],   # e.g. "LEARN_MORE"
                "value": {"link": destination_url},    # required — must match link above
            },
        },
    },
}

# Ad
{
    "name": variant.headline[:100],
    "adset_id": adset_id,
    "creative": {"creative_id": creative.get_id()},
    "status": "PAUSED",   # activate manually in Ads Manager after visual check
}
```
