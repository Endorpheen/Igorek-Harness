package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.io.OutputStream
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager

// The code carries the server error machine code (e.g., ``no_key`` for quota), so
// UI can display localized text instead of a generic message.
class BridgeException(val status: Int, message: String, val code: String? = null, val diagnostic: ErrorObservation? = null) : IOException(message)

class BridgeClient(
    baseUrl: String,
    private val token: String,
    pinnedFingerprint: String? = null,
) {
    internal val transportOwner = "bridge-http:" + connectionScope(baseUrl)
    internal var onDiagnostic: ((ErrorObservation) -> Unit)? = null
    internal var onSuccess: (() -> Unit)? = null
    internal var onRecovered: ((String) -> Unit)? = null
    private val failedReadOwners = java.util.concurrent.ConcurrentHashMap<String,String>()
    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }

    private val api = secureBridgeUrl(baseUrl) + "/mobile/v1"

    private val http: OkHttpClient = OkHttpClient.Builder()
        .retryOnConnectionFailure(false)
        .followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .let { builder -> applyPinning(builder, pinnedFingerprint) }
        .build()

    private val streamHttp: OkHttpClient = OkHttpClient.Builder()
        .retryOnConnectionFailure(false)
        .followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .let { builder -> applyPinning(builder, pinnedFingerprint) }
        .build()

    private val uploadHttp: OkHttpClient = OkHttpClient.Builder()
        .retryOnConnectionFailure(false)
        .followSslRedirects(false)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .let { builder -> applyPinning(builder, pinnedFingerprint) }
        .build()

    private fun applyPinning(builder: OkHttpClient.Builder, fingerprint: String?): OkHttpClient.Builder {
        val normalized = normalizeFingerprint(fingerprint)
        if (normalized == null) return builder
        val trustManager = FingerprintTrustManager(normalized)
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf(trustManager), null)
        val socketFactory: SSLSocketFactory = context.socketFactory
        // The certificate fingerprint replaces the verification of the CRL (Certificate Revocation List) chain and hostname:
        // The certificate is self-signed, and the address is an IP without SAN (Subject Alternative Name).
        return builder
            .sslSocketFactory(socketFactory, trustManager)
            .hostnameVerifier { _, _ -> true }
    }

    private fun requestBuilder(path: String): Request.Builder =
        Request.Builder()
            .url(api + path)
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/json")
            .header("X-Client-Operation-Id", java.util.UUID.randomUUID().toString())

    suspend fun browserPreview(after: String = ""): BrowserPreviewResponse = suspendCancellableCoroutine { continuation ->
        val query = if (after.matches(Regex("[a-f0-9]{64}"))) "?after=$after" else ""
        val call = http.newBuilder().callTimeout(12, TimeUnit.SECONDS).build()
            .newCall(requestBuilder("/browser/preview$query").get().build())
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (continuation.isActive) continuation.resumeWithException(e)
            }
            override fun onResponse(call: Call, response: Response) {
                try {
                    response.use {
                        if (!it.isSuccessful) throw BridgeException(it.code, "Browser preview unavailable")
                        val source = it.body?.source() ?: throw IOException("Empty browser preview")
                        if (source.request(460001)) throw IOException("Browser preview too large")
                        val result = parse<BrowserPreviewResponse>(source.buffer.readUtf8())
                        if (continuation.isActive) continuation.resume(result)
                    }
                } catch (error: Exception) {
                    if (continuation.isActive) continuation.resumeWithException(error)
                }
            }
        })
    }

    suspend fun selectBrowserTab(tabId: String): BrowserTabSelectionResponse = suspendCancellableCoroutine { continuation ->
        require(tabId.matches(Regex("[-a-zA-Z0-9]{1,64}")))
        val body = buildJsonObject { put("tab_id", tabId) }.toString()
        val call = http.newBuilder().callTimeout(12, TimeUnit.SECONDS).build().newCall(
            requestBuilder("/browser/select").post(body.toRequestBody("application/json".toMediaType())).build(),
        )
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (continuation.isActive) continuation.resumeWithException(e)
            }
            override fun onResponse(call: Call, response: Response) {
                try {
                    response.use {
                        if (!it.isSuccessful) throw BridgeException(it.code, "Browser tab selection unavailable")
                        val source = it.body?.source() ?: throw IOException("Empty browser tab selection")
                        if (source.request(8193)) throw IOException("Browser tab selection too large")
                        val result = parse<BrowserTabSelectionResponse>(source.buffer.readUtf8())
                        if (continuation.isActive) continuation.resume(result)
                    }
                } catch (error: Exception) {
                    if (continuation.isActive) continuation.resumeWithException(error)
                }
            }
        })
    }

    fun terminalTicket(sessionId: String): String = execute(
        requestBuilder("/sessions/${URLEncoder.encode(sessionId, "UTF-8")}/terminal")
            .post("{}".toRequestBody("application/json".toMediaType())).build(),
    ) { body -> (parse<JsonObject>(body)["ticket"] as JsonPrimitive).content }

    fun terminalSocket(listener: okhttp3.WebSocketListener): okhttp3.WebSocket =
        http.newBuilder().readTimeout(0, TimeUnit.SECONDS).build()
            .newWebSocket(requestBuilder("/terminal").build(), listener)

    private inline fun <reified T> parse(body: String): T = json.decodeFromString(body)

    private fun <T> execute(request: Request, decode: (String) -> T): T = executeWithClient(http, request, decode)

    private fun <T> executeWithClient(client: OkHttpClient, request: Request, decode: (String) -> T): T {
        val started=System.nanoTime()
        var attempt=1
        while(true) {
        val phase=java.util.concurrent.atomic.AtomicReference("connect")
        try {
        val remaining=(30000-(System.nanoTime()-started)/1000000).coerceAtLeast(1)
        val builder=client.newBuilder().eventListener(object:okhttp3.EventListener() {
            override fun secureConnectStart(call:Call) { phase.set("tls_handshake") }
            override fun requestHeadersStart(call:Call) { phase.set("request_send") }
            override fun responseHeadersStart(call:Call) { phase.set("http_body") }
            override fun responseBodyStart(call:Call) { phase.set("read") }
        })
        if(request.method in setOf("GET","HEAD"))builder.callTimeout(remaining,TimeUnit.MILLISECONDS)
        val runner=builder.build()
        runner.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val parsed = runCatching { parse<ErrorResponse>(body) }.getOrNull()
                val message = parsed?.error ?: "HTTP ${response.code}"
                val diagnostic=parsed?.diagnostic?.let(::sanitizeDiagnostic) ?: ErrorObservation(
                    code=classifyDiagnostic(status=response.code),component="bridge",operation=if(request.method=="GET")"read" else "write",
                    owner="bridge-operation:"+connectionScope(request.method+request.url.encodedPath), delivery="rejected",outcome="failed",
                    httpRequestId=response.header("x-request-id").orEmpty(),clientOperationId=request.header("X-Client-Operation-Id").orEmpty(),httpStatus=response.code)
                val retryAfter=parseRetryAfter(response.header("Retry-After"))
                throw BridgeException(response.code, message, parsed?.code?.takeIf { it.isNotBlank() }, diagnostic.copy(attempt=attempt,retryAfter=retryAfter ?: diagnostic.retryAfter))
            }
            val value=try { decode(body) } catch(error: kotlinx.serialization.SerializationException) {
                throw DiagnosticIOException(ErrorObservation(code="PROTOCOL.INVALID_RESPONSE",owner="bridge-operation:"+connectionScope(request.method+request.url.encodedPath),phase="response_validation",clientOperationId=request.header("X-Client-Operation-Id").orEmpty()),error)
            }
            runCatching { onSuccess?.invoke() }
            if(request.method=="GET")failedReadOwners.remove(request.url.encodedPath)?.let { owner -> runCatching { onRecovered?.invoke(owner) } }
            return value
        }
        } catch(error: Exception) {
            if(error is java.util.concurrent.CancellationException)throw error
            val diagnostic=(when(error){is BridgeException->error.diagnostic;is DiagnosticIOException->error.diagnostic;else->null}
                ?: ErrorObservation(code=classifyDiagnostic(error,phase=phase.get()),owner=transportOwner,operation=if(request.method=="GET")"read" else "write",phase=phase.get(),
                    delivery=if(phase.get()=="connect")"not_sent" else "unknown",outcome=if(request.method=="GET" || phase.get()=="connect")"failed" else "unknown",
                    clientOperationId=request.header("X-Client-Operation-Id").orEmpty(),nativeCode=error.javaClass.simpleName)).copy(attempt=attempt)
            val elapsed=(System.nanoTime()-started)/1000000
            val delay=maxOf(reconnectDelay(attempt-1),((diagnostic.retryAfter ?: 0.0)*1000).toLong())
            val retry=diagnosticRetryAllowed(diagnostic,request.method in setOf("GET","HEAD"),attempt,elapsed,delay)
            val observed=diagnostic.copy(nextRetryAt=if(retry)System.currentTimeMillis()+delay else null)
            if(request.method=="GET" && observed.owner!=transportOwner) {
                if(failedReadOwners.size>=100)failedReadOwners.clear()
                failedReadOwners[request.url.encodedPath]=observed.owner
            }
            runCatching { onDiagnostic?.invoke(observed) }
            if(!retry) {
                if(error is BridgeException)throw BridgeException(error.status,error.message.orEmpty(),error.code,observed)
                throw DiagnosticIOException(observed,error)
            }
            Thread.sleep(delay)
            attempt++
        }
        }
    }

    fun pair(baseUrl: String, code: String, name: String): PairResponse {
        val payload = json.encodeToString(
            PairCompleteRequest.serializer(),
            PairCompleteRequest(code = code, name = name),
        )
        val request = Request.Builder()
            .url(secureBridgeUrl(baseUrl) + "/mobile/v1/pair/complete")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun sessionListing(archivedOnly: Boolean = false): SessionsResponse {
        val query = if (archivedOnly) "?archived=1" else ""
        return execute(requestBuilder("/sessions$query").get().build()) { parse(it) }
    }

    fun sessions(archivedOnly: Boolean = false): List<SessionSummary> =
        sessionListing(archivedOnly).sessions

    fun agentSessions(sessionId: String): AgentSessionsResponse =
        execute(requestBuilder("/sessions/" + URLEncoder.encode(sessionId, "UTF-8") + "/agents").get().build()) { parse(it) }

    private val schedulesHttp by lazy { http.newBuilder().retryOnConnectionFailure(false).build() }

    fun schedules(): SchedulesResponse =
        executeWithClient(schedulesHttp, requestBuilder("/schedules").get().build()) { parse(it) }

    fun saveSchedule(fields: ScheduleFields, scheduleId: String? = null): SchedulesResponse {
        val payload = json.encodeToString(ScheduleFields.serializer(), fields).toRequestBody(JSON_MEDIA)
        val request = if (scheduleId == null) requestBuilder("/schedules").post(payload).build()
            else requestBuilder("/schedules/" + URLEncoder.encode(scheduleId, "UTF-8")).patch(payload).build()
        return executeWithClient(schedulesHttp, request) { parse(it) }
    }

    fun setScheduleEnabled(id: String, enabled: Boolean): SchedulesResponse {
        val payload = buildJsonObject { put("enabled", JsonPrimitive(enabled)) }.toString().toRequestBody(JSON_MEDIA)
        return executeWithClient(schedulesHttp, requestBuilder("/schedules/" + URLEncoder.encode(id, "UTF-8")).patch(payload).build()) { parse(it) }
    }

    fun runSchedule(id: String): SchedulesResponse = executeWithClient(schedulesHttp,
        requestBuilder("/schedules/" + URLEncoder.encode(id, "UTF-8") + "/run").post("{}".toRequestBody(JSON_MEDIA)).build()) { parse(it) }

    fun deleteSchedule(id: String): SchedulesResponse = executeWithClient(schedulesHttp,
        requestBuilder("/schedules/" + URLEncoder.encode(id, "UTF-8")).delete().build()) { parse(it) }

    fun sessionOptions(): SessionOptionsResponse =
        execute(requestBuilder("/session-options").get().build()) { parse(it) }

    fun mcp(action: String, fields: JsonObject): JsonObject {
        val payload = buildJsonObject { put("action", action); put("fields", fields) }
        return execute(requestBuilder("/mcp").post(payload.toString().toRequestBody("application/json".toMediaType())).build()) { parse(it) }
    }

    // OpenCode Go subscription quota: the same numbers as displayed in the web panel.
    fun quota(refresh: Boolean = false, profile: String = ""): QuotaResponse {
        if (profile.isNotBlank()) {
            try {
                return execute(requestBuilder("/provider-usage?profile=" + URLEncoder.encode(profile, "UTF-8") + if (refresh) "&refresh=1" else "").get().build()) { parse(it) }
            } catch (error: BridgeException) { if (error.status != 404 || error.code == "profile_unknown") throw error }
        }
        val query = if (refresh) "?refresh=1" else ""
        return execute(requestBuilder("/opencode-usage$query").get().build()) { parse(it) }
    }

    fun externalSessions(refresh: Boolean = false): ExternalSessionsResponse {
        val query = if (refresh) "?limit=1000&refresh=1" else "?limit=1000"
        return execute(requestBuilder("/external-sessions$query").get().build()) { parse(it) }
    }

    fun externalSession(source: String, sessionId: String, offset: Int = 0): ExternalSessionDetail {
        val encodedSource = URLEncoder.encode(source, "UTF-8").replace("+", "%20")
        val encodedId = URLEncoder.encode(sessionId, "UTF-8").replace("+", "%20")
        return execute(
            requestBuilder("/external-sessions/$encodedSource/$encodedId?offset=$offset&limit=1000").get().build()
        ) { parse(it) }
    }

    fun importExternalSession(
        source: String,
        sessionId: String,
        profile: String,
        cwd: String,
        key: String,
    ): ImportExternalSessionResponse {
        val encodedSource = URLEncoder.encode(source, "UTF-8").replace("+", "%20")
        val encodedId = URLEncoder.encode(sessionId, "UTF-8").replace("+", "%20")
        val body = json.encodeToString(
            ImportExternalSessionRequest.serializer(),
            ImportExternalSessionRequest(profile, cwd, key),
        )
        return execute(
            requestBuilder("/external-sessions/$encodedSource/$encodedId/import")
                .header("Content-Type", "application/json")
                .post(body.toRequestBody(JSON_MEDIA))
                .build()
        ) { parse(it) }
    }

    fun extensions(profile: String): ExtensionsResponse {
        val encoded = URLEncoder.encode(profile, "UTF-8").replace("+", "%20")
        return execute(requestBuilder("/extensions?profile=$encoded").get().build()) { parse(it) }
    }

    fun setSkillEnabled(skillId: String, enabled: Boolean): UpdateSkillResponse {
        val encoded = URLEncoder.encode(skillId, "UTF-8").replace("+", "%20")
        val body = json.encodeToString(UpdateSkillRequest.serializer(), UpdateSkillRequest(enabled))
        val request = requestBuilder("/extensions/skills/$encoded")
            .header("Content-Type", "application/json")
            .patch(body.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun archiveSessions(archived: Boolean = true): ArchiveSessionsResponse {
        val body = json.encodeToString(
            ArchiveSessionsRequest.serializer(),
            ArchiveSessionsRequest(archived),
        )
        val request = requestBuilder("/sessions")
            .header("Content-Type", "application/json")
            .patch(body.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun attachments(sessionId: String): List<AttachmentDto> =
        execute(requestBuilder("/sessions/$sessionId/files").get().build()) { parse<AttachmentsResponse>(it).files }

    fun sessionChanges(sessionId: String): ChangedFilesResponse =
        execute(requestBuilder("/sessions/$sessionId/changes").get().build()) { parse(it) }

    internal fun voiceRequest(path: String, body: String?): JsonObject {
        val request = requestBuilder(path)
        if (body == null) request.get() else request.post(body.toRequestBody(JSON_MEDIA))
        return execute(request.build()) { parse(it) }
    }

    internal fun changesReview(sessionId: String, query: String): ChangesReview =
        execute(requestBuilder("/sessions/$sessionId/changes/review?$query").get().build()) { parse(it) }

    internal fun changesReviewFile(sessionId: String, query: String): ReviewFile =
        execute(requestBuilder("/sessions/$sessionId/changes/review?$query").get().build()) { parse(it) }

    fun sessionChangesDiff(sessionId: String, path: String): FileDiffResponse {
        val encoded = URLEncoder.encode(path, "UTF-8").replace("+", "%20")
        return execute(requestBuilder("/sessions/$sessionId/changes/diff?path=$encoded").get().build()) { parse(it) }
    }

    fun sessionCheckpoints(sessionId: String, limit: Int = 50, offset: Int = 0): CheckpointListResponse =
        execute(requestBuilder("/sessions/$sessionId/checkpoints?limit=$limit&offset=$offset").get().build()) { parse(it) }

    fun checkpointChanges(sessionId: String, hash: String): ChangedFilesResponse {
        val encoded = URLEncoder.encode(hash, "UTF-8").replace("+", "%20")
        return execute(requestBuilder("/sessions/$sessionId/checkpoints/$encoded/changes").get().build()) { parse(it) }
    }

    fun checkpointDiff(sessionId: String, hash: String, path: String): FileDiffResponse {
        val encodedHash = URLEncoder.encode(hash, "UTF-8").replace("+", "%20")
        val encodedPath = URLEncoder.encode(path, "UTF-8").replace("+", "%20")
        return execute(
            requestBuilder("/sessions/$sessionId/checkpoints/$encodedHash/diff?path=$encodedPath").get().build()
        ) { parse(it) }
    }

    fun download(sessionId: String, file: AttachmentDto, output: OutputStream, progress: (Long) -> Unit) {
        val request = requestBuilder("/sessions/$sessionId/files/${file.id}").get().build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw BridgeException(response.code,
                if (response.code == 404 || response.code == 409) {
                    AppLocale.text("Файл изменился или удалён. Обновите чат.", "The file changed or was removed. Refresh the chat.")
                } else {
                    AppLocale.text("Не удалось скачать файл: HTTP ${response.code}", "Could not download the file: HTTP ${response.code}")
                })
            val body = response.body ?: throw IOException(AppLocale.text("Сервер вернул пустой ответ", "The server returned an empty response"))
            if (file.size !in 0..524288000L) {
                throw IOException(AppLocale.text("Файл превышает лимит 500 МБ", "The file exceeds the 500 MB limit"))
            }
            if (body.contentLength() != file.size) {
                throw IOException(AppLocale.text("Размер файла изменился. Обновите чат.", "The file size changed. Refresh the chat."))
            }
            body.byteStream().use { input ->
                val buffer = ByteArray(65536)
                var received = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    received += count
                    if (received > file.size) throw IOException(
                        AppLocale.text("Размер файла превышает заявленный", "The file is larger than declared")
                    )
                    output.write(buffer, 0, count)
                    progress(received)
                }
                if (received != file.size) throw IOException(
                    AppLocale.text("Загрузка оборвалась. Повторите скачивание.", "The download was interrupted. Try again.")
                )
                output.flush()
            }
        }
    }

    fun downloadUploadPreview(sessionId: String, file: UploadAttachmentDto): ByteArray {
        if (!sessionId.matches(Regex("[A-Za-z0-9_-]{1,160}")) || !file.uploadId.matches(Regex("[0-9a-f]{32}")) ||
            !file.isImage || file.size !in 1..8388608L) throw IOException("Image preview is unavailable")
        val request = requestBuilder("/sessions/$sessionId/uploads/${file.uploadId}").get().build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw BridgeException(response.code,
                AppLocale.text("Изображение недоступно", "Image is unavailable"))
            val body = response.body ?: throw IOException("Empty image response")
            if (body.contentLength() != file.size ||
                response.header("Content-Type")?.startsWith("image/", ignoreCase = true) != true) {
                throw IOException("Invalid image response")
            }
            val output = java.io.ByteArrayOutputStream()
            body.byteStream().use { input ->
                val buffer = ByteArray(16384)
                var received = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    received += count
                    if (received > file.size) throw IOException("Image exceeds declared size")
                    output.write(buffer, 0, count)
                }
                if (received != file.size) throw IOException("Incomplete image response")
            }
            return output.toByteArray()
        }
    }

    fun createSession(profile: String, cwd: String, key: String): SessionDetail {
        val body = json.encodeToString(CreateSessionRequest.serializer(), CreateSessionRequest(profile, cwd, key, activateProfile = true))
        return execute(requestBuilder("/sessions").post(body.toRequestBody(JSON_MEDIA)).build()) { parse(it) }
    }

    fun switchSessionProfile(sessionId: String, profile: String): SessionDetail {
        require(sessionId.matches(Regex("[A-Za-z0-9_-]{1,160}")))
        require(profile.isNotBlank())
        val body = buildJsonObject { put("profile", profile) }.toString()
        return execute(requestBuilder("/sessions/$sessionId/profile")
            .post(body.toRequestBody(JSON_MEDIA)).build()) { parse(it) }
    }

    fun updateSession(sessionId: String, title: String? = null, archived: Boolean? = null, pinned: Boolean? = null): JsonObject {
        val body = json.encodeToString(UpdateSessionRequest.serializer(), UpdateSessionRequest(title, archived, pinned))
        val request = requestBuilder("/sessions/$sessionId")
            .header("Content-Type", "application/json")
            .patch(body.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun session(sessionId: String): SessionDetail =
        execute(requestBuilder("/sessions/$sessionId").get().build()) { parse(it) }

    fun sessionEvents(sessionId: String, afterSeq: Long = 0, limit: Int = 500): List<BridgeEvent> {
        val request = requestBuilder("/sessions/$sessionId/events?after=$afterSeq&limit=$limit").get().build()
        return execute(request) { parse<EventsResponse>(it).events }
    }

    fun sendMessage(
        sessionId: String,
        text: String,
        idempotencyKey: String?,
        uploadIds: List<String> = emptyList(),
        skillIds: List<String> = emptyList(),
    ): SendMessageResponse {
        val payload = json.encodeToString(
            SendMessageRequest.serializer(),
            SendMessageRequest(
                text = text,
                idempotencyKey = idempotencyKey,
                uploadIds = uploadIds,
                skillIds = skillIds,
                activateProfile = true,
            ),
        )
        val request = requestBuilder("/sessions/$sessionId/messages")
            .apply { if(!idempotencyKey.isNullOrBlank())header("X-Client-Operation-Id",idempotencyKey) }
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun sendMessageNow(sessionId: String, taskId: String, messageId: String): JsonObject {
        val payload = buildJsonObject { put("task_id", JsonPrimitive(taskId)) }
        val body = json.encodeToString(JsonObject.serializer(), payload)
        val encodedSession = URLEncoder.encode(sessionId, "UTF-8").replace("+", "%20")
        val encodedMessage = URLEncoder.encode(messageId, "UTF-8").replace("+", "%20")
        val request = requestBuilder("/sessions/$encodedSession/messages/$encodedMessage/send-now")
            .header("Content-Type", "application/json")
            .post(body.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun cancelMessage(sessionId: String, taskId: String, messageId: String): JsonObject {
        val payload = buildJsonObject { put("task_id", JsonPrimitive(taskId)) }
        val body = json.encodeToString(JsonObject.serializer(), payload)
        val encodedSession = URLEncoder.encode(sessionId, "UTF-8").replace("+", "%20")
        val encodedMessage = URLEncoder.encode(messageId, "UTF-8").replace("+", "%20")
        val request = requestBuilder("/sessions/$encodedSession/messages/$encodedMessage/cancel")
            .header("Content-Type", "application/json")
            .post(body.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun upload(sessionId: String, data: ByteArray, name: String, mimeType: String): UploadResponse {
        val effectiveMime = mimeType.ifBlank { "application/octet-stream" }
        val encodedName = URLEncoder.encode(name, "UTF-8").replace("+", "%20")
        val request = requestBuilder("/sessions/$sessionId/uploads")
            .header("Content-Type", effectiveMime)
            .header("X-File-Name", encodedName)
            .post(data.toRequestBody(effectiveMime.toMediaType()))
            .build()
        uploadHttp.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val message = runCatching { parse<ErrorResponse>(body).error }.getOrNull()
                    ?: "HTTP ${response.code}"
                throw BridgeException(response.code, message)
            }
            return parse(body)
        }
    }

    fun approvals(sessionId: String?): List<ApprovalDto> {
        val query = if (sessionId.isNullOrBlank()) "" else "?session_id=$sessionId"
        return execute(requestBuilder("/approvals$query").get().build()) { parse<ApprovalsResponse>(it).approvals }
    }

    fun approvalSettings(): ApprovalSettingsResponse =
        execute(requestBuilder("/approval-settings").get().build()) { parse(it) }

    fun setApprovalSettings(enabled: Boolean): ApprovalSettingsResponse {
        val payload = json.encodeToString(
            ApprovalSettingsRequest.serializer(),
            ApprovalSettingsRequest(enabled),
        )
        val request = requestBuilder("/approval-settings")
            .header("Content-Type", "application/json")
            .patch(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun securitySettings(): SecuritySettingsResponse =
        execute(requestBuilder("/security-settings").get().build()) { parse(it) }

    fun setSecurityLevel(
        level: String,
        ttlS: Long? = null,
        confirm: Boolean = false,
    ): SecuritySettingsResponse {
        val payload = json.encodeToString(
            SecuritySettingsRequest.serializer(),
            SecuritySettingsRequest(level = level, ttlS = ttlS, confirm = confirm),
        )
        val request = requestBuilder("/security-settings")
            .header("Content-Type", "application/json")
            .patch(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun sandboxSettings(): SandboxSettingsResponse =
        execute(requestBuilder("/sandbox-settings").get().build()) { parse(it) }

    fun setSandboxEnabled(enabled: Boolean): SandboxSettingsResponse {
        val payload = json.encodeToString(
            SandboxSettingsRequest.serializer(),
            SandboxSettingsRequest(enabled),
        )
        val request = requestBuilder("/sandbox-settings")
            .header("Content-Type", "application/json")
            .patch(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun resolveApproval(taskId: String, requestId: String, decision: String): JsonObject {
        val payload = json.encodeToString(DecisionRequest.serializer(), DecisionRequest(decision))
        val request = requestBuilder("/tasks/$taskId/approvals/$requestId")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun resolveSudo(requestId: String, decision: String, password: String): JsonObject {
        if (!api.startsWith("https://")) throw BridgeException(403, "Sudo requires HTTPS")
        val payload = json.encodeToString(
            JsonObject.serializer(),
            JsonObject(mapOf(
                "decision" to JsonPrimitive(decision),
                "password" to JsonPrimitive(password),
            )),
        )
        val request = requestBuilder("/sudo-requests/$requestId")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun answerUserInput(taskId: String, requestId: String, body: UserInputRequest): JsonObject {
        val payload = json.encodeToString(UserInputRequest.serializer(), body)
        val request = requestBuilder("/tasks/$taskId/user-input/$requestId")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun cancelTask(taskId: String, mode: String): JsonObject {
        val payload = json.encodeToString(CancelRequest.serializer(), CancelRequest(mode))
        val request = requestBuilder("/tasks/$taskId/cancel")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun cancelSession(sessionId: String, mode: String): JsonObject {
        val payload = json.encodeToString(CancelRequest.serializer(), CancelRequest(mode))
        val request = requestBuilder("/sessions/$sessionId/cancel")
            .header("Content-Type", "application/json")
            .post(payload.toRequestBody(JSON_MEDIA))
            .build()
        return execute(request) { parse(it) }
    }

    fun devices(): DevicesResponse =
        execute(requestBuilder("/devices").get().build()) { parse<DevicesResponse>(it) }

    fun revokeDevice(deviceId: String): JsonObject =
        execute(requestBuilder("/devices/$deviceId").delete().build()) { parse(it) }

    fun deleteDevice(deviceId: String): JsonObject =
        execute(requestBuilder("/devices/$deviceId?purge=1").delete().build()) {
            parse(it)
        }

    fun purgeRevokedDevices(): JsonObject =
        execute(
            requestBuilder("/devices/purge-revoked")
                .post("{}".toRequestBody(JSON_MEDIA))
                .build(),
        ) { parse(it) }

    fun diagnostics(): JsonObject = execute(requestBuilder("/diagnostics").get().build()) { parse(it) }

    fun operationReceipt(key:String,sessionId:String):JsonObject = execute(requestBuilder("/operations/"+URLEncoder.encode(key,"UTF-8")+"?session_id="+URLEncoder.encode(sessionId,"UTF-8")).get().build()) { parse(it) }

    fun openEventStream(
        afterSeq: Long,
        sessionId: String?,
        onEvent: (BridgeEvent) -> Unit,
        onClosed: (Throwable?) -> Unit,
    ): Call {
        val query = buildString {
            append("?after=").append(afterSeq)
            if (!sessionId.isNullOrBlank()) append("&session_id=").append(sessionId)
        }
        val request = requestBuilder("/events$query")
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .get()
            .build()
        val call = streamHttp.newCall(request)
        Thread {
            try {
                call.execute().use { response ->
                    if (!response.isSuccessful) {
                        onClosed(BridgeException(response.code, "stream failed"))
                        return@use
                    }
                    val source = response.body?.source()
                    if (source == null) {
                        onClosed(IOException("empty stream body"))
                        return@use
                    }
                    while (true) {
                        val line = source.readUtf8Line() ?: break
                        if (!line.startsWith(DATA_PREFIX)) continue
                        val raw = line.substring(DATA_PREFIX.length)
                        val event = parse<BridgeEvent>(raw)
                        onEvent(event)
                    }
                    onClosed(null)
                }
            } catch (error: Throwable) {
                onClosed(error)
            }
        }.apply { isDaemon = true }.start()
        return call
    }

    private class FingerprintTrustManager(private val expected: String) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            throw CertificateException("client certificates are not supported")
        }

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw CertificateException("empty certificate chain")
            val actual = sha256(leaf.encoded)
            if (!MessageDigest.isEqual(actual.toByteArray(), expected.toByteArray())) {
                throw CertificateException("certificate fingerprint mismatch")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        private const val DATA_PREFIX = "data: "

        fun normalizeFingerprint(value: String?): String? {
            val cleaned = value?.replace(":", "")?.replace(" ", "")?.trim()?.lowercase()
            return if (cleaned.isNullOrEmpty()) null else cleaned
        }

        fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }
    }
}
