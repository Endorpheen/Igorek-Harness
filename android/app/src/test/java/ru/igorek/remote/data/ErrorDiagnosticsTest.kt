package ru.igorek.remote.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.intOrNull
import org.junit.Assert.*
import org.junit.Test
import javax.net.ssl.SSLHandshakeException
import java.security.cert.CertificateException
import java.net.SocketTimeoutException

class ErrorDiagnosticsTest {
    @Test fun sharedNativeAndHttpFixture() {
        val text=javaClass.classLoader!!.getResourceAsStream("classification.json")!!.bufferedReader().readText()
        for(row in Json.parseToJsonElement(text).jsonArray) {
            val fields=row.jsonObject.getValue("input").jsonObject
            assertEquals(row.jsonObject.getValue("code").jsonPrimitive.content,classifyDiagnostic(
                nativeCode=fields["native_code"]?.jsonPrimitive?.content.orEmpty(),
                status=fields["status"]?.jsonPrimitive?.intOrNull,phase=fields["phase"]?.jsonPrimitive?.content ?: "unknown"))
        }
    }
    @Test fun certificateCauseIsNotAnOrdinaryNetworkRetry() {
        val cause=SSLHandshakeException("fixture").apply { initCause(CertificateException("fixture")) }
        assertEquals("TLS.CERTIFICATE_REJECTED",classifyDiagnostic(cause))
        assertEquals("NET.READ_TIMEOUT",classifyDiagnostic(SocketTimeoutException("fixture")))
        assertFalse(diagnosticRetryAllowed(ErrorObservation(code="TLS.CERTIFICATE_REJECTED"),true,1,0,100))
    }
    @Test fun stableRecoveryIsOwnedAndDoesNotResolveUnknownWrites() {
        val e=ErrorObservation(code="NET.CONNECTION_RESET",owner="fixture")
        var list=mergeNotification(emptyList(),"first","CONNECTION",100,e)
        list=list.map { it.copy(read=true) }
        list=mergeNotification(list,"translated","CONNECTION",200,e.copy(eventId="second"))
        assertEquals(1,list.size);assertEquals(2,list[0].count);assertTrue(list[0].read)
        assertEquals("observed",recoverNotifications(list,"other",10000,300)[0].state)
        assertEquals("observed",recoverNotifications(list,"fixture",1000,300)[0].state)
        list=recoverNotifications(list,"fixture",5000,300)
        assertEquals("recovered",list[0].state)
        list=mergeNotification(list,"new outage","CONNECTION",400,e.copy(eventId="third"))
        assertEquals(2,list.size);assertFalse(list.last().read)
        val unknown=mergeNotification(emptyList(),"fixture","OPERATION",100,e.copy(outcome="unknown",operation="write"))
        assertEquals("needs_owner",recoverNotifications(unknown,"fixture",10000,500)[0].state)
    }
    @Test fun replayedEventDoesNotIncrementAnIncident() {
        val error=ErrorObservation(code="NET.CONNECTION_RESET",owner="fixture")
        val first=mergeNotification(emptyList(),"a","CONNECTION",100,error)
        assertEquals(first,mergeNotification(first,"a","CONNECTION",200,error))
    }
    @Test fun retryBudgetAndReconnectDelayAreBounded() {
        assertEquals(30000L,reconnectDelay(999))
        assertFalse(diagnosticRetryAllowed(ErrorObservation(code="NET.CONNECTION_RESET"),false,1,0,100))
        assertFalse(diagnosticRetryAllowed(ErrorObservation(code="NET.CONNECTION_RESET"),true,3,0,100))
        assertFalse(diagnosticRetryAllowed(ErrorObservation(code="NET.CONNECTION_RESET"),true,1,29999,100))
    }
    @Test fun metadataRejectsRawUrlsAndUnknownCodes() {
        val safe=sanitizeDiagnostic(ErrorObservation(code="unknown",owner="https://user:pass@example.test/?token=fixture",model="org/model"))
        assertEquals("INTERNAL.UNEXPECTED",safe.code);assertEquals("",safe.owner);assertEquals("org/model",safe.model)
    }
}
