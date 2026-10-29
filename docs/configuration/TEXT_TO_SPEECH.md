# Reading assistant messages aloud

Desktop settings → **Озвучивание сообщений** configures an optional TTS service.
Enable the feature, enter its API base URL (including `/v1`), model ID and voice,
then save. **Получить модели** reads the server catalog; **Проверить голос** makes
a short preview. Existing realtime voice settings are independent.

Completed assistant messages show **Озвучить** without requiring mouse hover. Playback starts only when pressed;
press again to stop pending download or playback. Long text is split into chunks
of at most 1,000 characters and requested sequentially. Changing settings cancels
pending synthesis. Audio is held in memory and temporary object URLs are revoked.
The chat server explicitly serves the player asset and allows local Blob audio
through its media CSP; external media origins remain blocked.
This feature is Desktop-only; it does not add an Android playback interface.

The service must support `GET /models` and `POST /audio/speech` below the API base.
Speech JSON includes `model`, `voice`, `input` and `response_format: "wav"`.
Responses must be RIFF/WAVE audio with an audio/wav-compatible Content-Type.
Limits: 10 seconds/256 KiB for catalog responses and 180 seconds/20 MiB for a
speech chunk. Redirects are refused. API-key authentication, streaming and voice
reference uploads are not part of this preview. Use a trusted reachable service;
text is sent to the configured address only on a playback request.

Example for a separately installed Audio8 service: API URL
`http://127.0.0.1:8092/v1`, model `audio8-realistic-0.6b`, voice `default`.
These are example values, not an installed portable default. Model weights and
servers are not bundled with Igorek. Audio8 supports reference-based cloning;
this integration currently uses its default voice without a reference.

A measured Ryzen 9 5950X / RTX 4070 12 GB setup kept the codec on CPU and the
0.6B autoregressive model on GPU: about 1.3 GiB Torch reservation plus driver
context and about 3.5 GiB peak process RAM. One Russian HTTP sample produced
5.25 seconds of audio in 12.03 seconds. This is a local measurement, not a
realtime guarantee. The 2.56 GB download includes a separate codec; the 0.6B
parameter count does not describe the entire synthesis stack.
