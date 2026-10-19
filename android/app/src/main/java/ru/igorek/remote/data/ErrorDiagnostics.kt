package ru.igorek.remote.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.SocketException
import java.net.UnknownHostException
import java.security.cert.CertificateException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import javax.net.ssl.SSLException
import java.util.UUID
import java.util.concurrent.CancellationException

@Serializable
data class ErrorObservation(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("event_id") val eventId: String = UUID.randomUUID().toString(),
    @SerialName("observed_at_ms") val observedAt: Long = System.currentTimeMillis(),
    val code: String = "INTERNAL.UNEXPECTED",
    val component: String = "mobile",
    val operation: String = "read",
    @SerialName("endpoint_role") val endpointRole: String = "mobile_to_bridge",
    val owner: String = "",
    val phase: String = "unknown",
    val delivery: String = "unknown",
    val outcome: String = "failed",
    @SerialName("http_request_id") val httpRequestId: String = "",
    @SerialName("client_operation_id") val clientOperationId: String = "",
    @SerialName("provider_request_id") val providerRequestId: String = "",
    @SerialName("task_id") val taskId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val profile: String = "",
    val model: String = "",
    @SerialName("http_status") val httpStatus: Int? = null,
    @SerialName("native_code") val nativeCode: String = "",
    val attempt: Int = 1,
    @SerialName("next_retry_at_ms") val nextRetryAt: Long? = null,
    @SerialName("retry_after_s") val retryAfter: Double? = null,
)

class DiagnosticIOException(val diagnostic: ErrorObservation, cause: Throwable) : IOException(errorMessage(diagnostic), cause)

internal fun errorMessage(error: ErrorObservation): String {
    val value = ErrorCatalog.codes[error.code] ?: ErrorCatalog.codes.getValue("INTERNAL.UNEXPECTED")
    return AppLocale.text(value.ru, value.en) + if (error.outcome == "unknown") AppLocale.text(
        " Результат действия пока не подтверждён. Не отправляйте его повторно до проверки.",
        " The outcome is unconfirmed. Check it before sending again.") else ""
}

internal fun safeDiagnosticText(text: String): String {
    var value=text.take(16000).replace(Regex("(?i)Bearer\\s+[^\\s\"'<>]+"),"Bearer [REDACTED]")
    value=value.replace(Regex("(?i)([\"']?(?:access_token|refresh_token|id_token|api[_-]?key|authorization|cookie|password|token|secret)[\"']?\\s*[:=]\\s*)(?:\"[^\"]*\"|'[^']*'|[^\\s,;]+)")) { it.groupValues[1]+"[REDACTED]" }
    value=value.replace(Regex("https?://[^\\s<>\"']+")) { match -> runCatching {
        val uri=java.net.URI(match.value)
        java.net.URI(uri.scheme,null,uri.host,uri.port,uri.path,null,null).toString()
    }.getOrDefault("[URL]") }
    return value.take(1000)
}

internal fun classifyDiagnostic(error: Throwable? = null, nativeCode: String = "", status: Int? = null, phase: String = "unknown"): String {
    var current = error
    var tlsEof=false
    val seen = mutableSetOf<Throwable>()
    repeat(5) {
        if (current == null || !seen.add(current!!)) return@repeat
        when (current) {
            is SSLPeerUnverifiedException, is CertificateException -> return "TLS.CERTIFICATE_REJECTED"
            is UnknownHostException -> return "NET.DNS_FAILURE"
            is SocketTimeoutException -> return if (phase == "connect") "NET.CONNECT_TIMEOUT" else "NET.READ_TIMEOUT"
            is ConnectException -> return "NET.CONNECTION_REFUSED"
            is CancellationException -> return "OPERATION.CANCELLED"
            is java.io.EOFException -> tlsEof=true
        }
        current = current?.cause
    }
    if (error is SSLException && tlsEof) return "TLS.CONNECTION_INTERRUPTED"
    if (error is SSLHandshakeException || error is SSLException) return "TLS.PROTOCOL_FAILURE"
    if (error is SocketException) return "NET.CONNECTION_RESET"
    val native = mapOf("EAI_AGAIN" to "NET.DNS_FAILURE", "ENOTFOUND" to "NET.DNS_FAILURE",
        "ECONNREFUSED" to "NET.CONNECTION_REFUSED", "ECONNRESET" to "NET.CONNECTION_RESET", "ECONNABORTED" to "NET.CONNECTION_RESET",
        "EPIPE" to "NET.CONNECTION_RESET", "ENETUNREACH" to "NET.OFFLINE", "ENETDOWN" to "NET.OFFLINE", "ENOSPC" to "STORAGE.FULL",
        "CERTIFICATE_REJECTED" to "TLS.CERTIFICATE_REJECTED", "INVALID_RESPONSE" to "PROTOCOL.INVALID_RESPONSE", "CANCELLED" to "OPERATION.CANCELLED",
        "TLS_EOF" to "TLS.CONNECTION_INTERRUPTED", "TRANSPORT_UNKNOWN" to "NET.TRANSPORT_FAILURE")
    native[nativeCode]?.let { return it }
    if (nativeCode in setOf("TIMEOUT", "ETIMEDOUT", "ESOCKETTIMEDOUT")) return if (phase == "connect") "NET.CONNECT_TIMEOUT" else "NET.READ_TIMEOUT"
    return when {
        status == 401 || status == 403 -> "AUTH.REJECTED"
        status == 429 -> "HTTP.RATE_LIMITED"
        status == 408 || status == 504 -> "NET.READ_TIMEOUT"
        status in setOf(500, 502, 503, 529) -> "HTTP.SERVICE_UNAVAILABLE"
        status != null && status >= 400 -> "HTTP.REQUEST_REJECTED"
        error is IOException -> "NET.TRANSPORT_FAILURE"
        else -> "INTERNAL.UNEXPECTED"
    }
}

internal fun connectionScope(value: String): String = java.security.MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray()).take(12).joinToString("") { "%02x".format(it.toInt() and 255) }

internal fun sanitizeDiagnostic(value: ErrorObservation): ErrorObservation {
    fun id(text: String) = text.takeIf { it.matches(Regex("[A-Za-z0-9_.:-]{1,160}")) }.orEmpty()
    fun model(text: String) = text.takeIf { it.matches(Regex("[A-Za-z0-9_./:-]{1,160}")) && !it.contains("://") }.orEmpty()
    return value.copy(schemaVersion=1,code=value.code.takeIf { it in ErrorCatalog.codes } ?: "INTERNAL.UNEXPECTED",
        eventId=id(value.eventId), component=id(value.component), operation=id(value.operation), owner=id(value.owner), phase=id(value.phase),
        endpointRole=id(value.endpointRole), delivery=id(value.delivery), outcome=id(value.outcome), httpRequestId=id(value.httpRequestId),
        clientOperationId=id(value.clientOperationId), providerRequestId=id(value.providerRequestId), taskId=id(value.taskId), sessionId=id(value.sessionId),
        profile=model(value.profile), model=model(value.model), nativeCode=id(value.nativeCode), attempt=value.attempt.coerceIn(1, 1000000),
        observedAt=value.observedAt.coerceAtLeast(0),nextRetryAt=value.nextRetryAt?.coerceAtLeast(0),
        retryAfter=value.retryAfter?.takeIf { it.isFinite() && it in 0.0..900.0 })
}

internal fun diagnosticFingerprint(value: ErrorObservation): String = listOf(value.owner,value.endpointRole,value.component,value.code,
    if(value.outcome=="unknown")value.operation else "read").joinToString("|")

internal fun reconnectDelay(attempt: Int, random: Double = Math.random()): Long =
    if(attempt>=3) 30000L else (minOf(10000.0,500.0*Math.pow(2.0,attempt.coerceAtLeast(0).toDouble()))*(0.5+0.5*random.coerceIn(0.0,1.0))).toLong()

internal fun diagnosticRetryAllowed(error: ErrorObservation, replaySafe: Boolean, attempt: Int, elapsedMs: Long, delayMs: Long): Boolean =
    replaySafe && (error.httpStatus==null || error.httpStatus in setOf(408,429,500,502,503,504,529)) &&
        ErrorCatalog.codes[error.code]?.retryable == true && attempt < 3 && elapsedMs + delayMs < 30000

internal fun parseRetryAfter(value:String?, now:Long=System.currentTimeMillis()):Double? {
    if(value==null)return null
    val seconds=value.toDoubleOrNull() ?: runCatching {
        (java.time.ZonedDateTime.parse(value,java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli()-now)/1000.0
    }.getOrNull() ?: return null
    return seconds.takeIf { it.isFinite() && it in 0.0..900.0 }
}
