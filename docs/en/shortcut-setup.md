# Shortcut setup

## Language

- English: [Shortcut setup](shortcut-setup.md)
- Chinese: [快捷指令配置](../shortcut-setup.md)

## Scope

This document focuses only on the webhook-sending part of your iPhone Shortcut.

Keep your existing menu, version checks, and other navigation logic unchanged.

## Current request contract

The current webhook endpoint is:

- `POST /v1/webhook/ocr`

Required header:

- `X-Webhook-Token`

Current JSON fields:

- `original_text`
- `source`
- `raw_ocr`
- optional: `book_alias`
- optional: `year`

Do **not** send Feishu secrets or identifiers from Shortcut.

Never send:

- `FEISHU_APP_SECRET`
- `app_token`
- `table_id`
- `record_id`

## Recommended flow

Recommended user flow:

1. OCR the screenshot
2. Let the user review or correct the text
3. Build the webhook request body
4. Send it to your public webhook URL
5. Parse the JSON response and show a success message

## Which Shortcut part to change

Only change the actions around the webhook request.

Typical actions to inspect:

- the `Text` action that stores the webhook URL
- the `Get Contents of URL` action
- the JSON body construction step
- the response parsing step

## Webhook URL

Put your actual public endpoint into the URL-holding action.

Example:

```text
https://hook.example.com/v1/webhook/ocr
```

If you are using a NAS, Windows host, or other self-hosted machine, prefer a stable HTTPS public URL.

## Why `Current Date` / `Format Date` should be removed

The current service no longer needs Shortcut to generate time fields.

Reason:

- the webhook only needs raw OCR business text
- Feishu automation remains responsible for downstream derived fields
- keeping time generation in Shortcut only increases coupling and confusion

So if your old Shortcut still builds `time` or similar request fields, remove that part.

## Configure `Get Contents of URL`

Recommended request settings:

- Method: `POST`
- Headers:
  - `Content-Type: application/json`
  - `X-Webhook-Token: <your real token>`
- Request Body: JSON

### Example JSON body

```json
{
  "original_text": "OCR extracted text",
  "source": "ios-shortcuts",
  "raw_ocr": "OCR extracted text",
  "book_alias": "2026"
}
```

If you prefer year-based routing, you may send:

```json
{
  "original_text": "OCR extracted text",
  "source": "ios-shortcuts",
  "raw_ocr": "OCR extracted text",
  "year": 2026
}
```

If you omit both `book_alias` and `year`, the service uses the current `default_alias`.

## Meaning of each field

- `original_text`: the final text that should be written into Feishu `原始信息`
- `source`: a simple source marker such as `ios-shortcuts`
- `raw_ocr`: the raw OCR output before or alongside manual cleanup
- `book_alias`: non-secret yearly target selector such as `2026`
- `year`: optional numeric selector if you prefer number-based routing

## Token requirement

`X-Webhook-Token` must use the real runtime value.

Do not leave placeholders like:

```text
replace_me_with_long_random_secret
```

If you do, the service will return `401 invalid webhook token`.

## Recommended place for manual confirmation

The recommended place for manual confirmation is:

- after OCR
- before sending the webhook

This keeps the Feishu-side `原始信息` closer to the final human-confirmed text.

## Parse the response

A successful response looks like this:

```json
{
  "success": true,
  "request_id": "req_xxxxxxxxxxxx",
  "record_id": "recXXXXXXXX",
  "book_alias": "2026",
  "message": "configured record updated and 原始信息 updated"
}
```

Recommended parsing approach:

1. `Get Dictionary from Input` should still use the default `Input`
2. `Get Value for Dictionary Value` should manually use the key `message`
3. optionally also read `book_alias` or `record_id` for debugging

Then show `message` as the success toast or result text.

## Common mistakes

### 1. Still using old fields `text` / `time`

Fix:

- change the body to `original_text`, `source`, `raw_ocr`
- optionally add `book_alias` or `year`

### 2. Sending Feishu IDs from Shortcut

Fix:

- keep Feishu credentials and identifiers on the server
- only send selector fields like `book_alias`

### 3. Wrong token placeholder

Fix:

- replace it with the real runtime token used by the server

## Recommended verification

1. run `GET /health`
2. send one real Shortcut request
3. confirm the response contains `success: true`
4. confirm Feishu updated the expected target book
5. confirm downstream Feishu automations still run
