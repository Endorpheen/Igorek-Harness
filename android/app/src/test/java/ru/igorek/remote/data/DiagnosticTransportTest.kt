package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test
import java.util.Collections

class DiagnosticTransportTest {
    private fun serve(responses:List<Pair<Int,String?>>, block:(BridgeClient,MutableList<String>)->Unit) {
        val server=BridgeTlsFixture.server(1)
        val requests=Collections.synchronizedList(mutableListOf<String>())
        val worker=Thread {
            for((status,body) in responses) {
                server.accept().use { socket ->
                    socket.soTimeout=5000
                    val reader=socket.getInputStream().bufferedReader()
                    val line=reader.readLine()
                    var length=0
                    while(true) {
                        val header=reader.readLine() ?: break
                        if(header.isEmpty())break
                        if(header.startsWith("Content-Length:",ignoreCase=true))length=header.substringAfter(':').trim().toInt()
                    }
                    var remaining=length
                    while(remaining>0){if(reader.read()==-1)break;remaining--}
                    requests.add(line)
                    if(body!=null) {
                        val bytes=body.toByteArray()
                        socket.getOutputStream().write(("HTTP/1.1 $status Fixture\r\nContent-Length: ${bytes.size}\r\nX-Request-Id: req_fixture12345678\r\nConnection: close\r\n\r\n").toByteArray()+bytes)
                        socket.getOutputStream().flush()
                    }
                }
            }
        }.apply { isDaemon=true;start() }
        try { block(BridgeTlsFixture.client(server,"fixture-token"),requests) }
        finally { server.close();worker.join(5000) }
    }

    @Test fun safeReadRetries503AndPreservesRequestCorrelation() {
        serve(listOf(503 to """{"error":"fixture unavailable"}""",200 to """{"sessions":[]}""")) { client,requests ->
            val errors=mutableListOf<ErrorObservation>();val recovered=mutableListOf<String>()
            client.onDiagnostic={errors.add(it)};client.onRecovered={recovered.add(it)}
            assertTrue(client.sessionListing().sessions.isEmpty())
            assertEquals(2,requests.size);assertEquals(1,errors.size)
            assertEquals("HTTP.SERVICE_UNAVAILABLE",errors.single().code)
            assertEquals("req_fixture12345678",errors.single().httpRequestId)
            assertFalse(errors.single().clientOperationId.isBlank())
            assertEquals(listOf(errors.single().owner),recovered)
        }
    }

    @Test fun lostResponseAfterReceivingWriteDoesNotReplayIt() {
        serve(listOf(200 to null)) { client,requests ->
            val error=assertThrows(DiagnosticIOException::class.java) { client.sendMessage("fixture","message","fixture-idempotency-key") }
            assertEquals(1,requests.size)
            assertEquals("unknown",error.diagnostic.outcome)
            assertEquals(1,error.diagnostic.attempt)
        }
    }

    @Test fun malformedSuccessIsProtocolFailureWithoutNetworkRetry() {
        serve(listOf(200 to "not-json")) { client,requests ->
            val error=assertThrows(DiagnosticIOException::class.java) { client.sessionListing() }
            assertEquals("PROTOCOL.INVALID_RESPONSE",error.diagnostic.code)
            assertEquals(1,requests.size)
        }
    }

    @Test fun rejectedWriteIsNotAutomaticallySentAgain() {
        serve(listOf(503 to """{"error":"fixture unavailable"}""")) { client,requests ->
            val error=assertThrows(BridgeException::class.java) { client.sendMessage("fixture","message","fixture-key") }
            assertEquals(503,error.status);assertEquals(1,requests.size)
            assertEquals("rejected",error.diagnostic!!.delivery)
        }
    }
}
