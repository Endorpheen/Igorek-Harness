package ru.igorek.remote.data

import java.net.InetAddress
import java.net.ServerSocket
import java.security.KeyStore
import java.security.MessageDigest
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

internal object BridgeTlsFixture {
    private val store = KeyStore.getInstance("PKCS12").apply {
        BridgeTlsFixture::class.java.getResourceAsStream("/loopback-test.p12").use {
            load(checkNotNull(it), "fixture-only".toCharArray())
        }
    }
    val fingerprint: String = MessageDigest.getInstance("SHA-256")
        .digest(store.getCertificate("loopback").encoded).joinToString("") { "%02x".format(it.toInt() and 255) }
    private val context = SSLContext.getInstance("TLS").apply {
        val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply {
            init(store, "fixture-only".toCharArray())
        }
        init(managers.keyManagers, null, null)
    }
    fun server(backlog: Int = 1): ServerSocket =
        context.serverSocketFactory.createServerSocket(0, backlog, InetAddress.getByName("127.0.0.1"))
    fun client(server: ServerSocket, token: String): BridgeClient =
        BridgeClient("https://127.0.0.1:${server.localPort}", token, fingerprint)
}
