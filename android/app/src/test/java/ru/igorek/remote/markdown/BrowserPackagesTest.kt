package ru.igorek.remote.markdown

import org.junit.Assert.assertEquals
import org.junit.Test
import ru.igorek.remote.ui.markdown.browserPackages

class BrowserPackagesTest {
    @Test
    fun `system resolver is not mistaken for a browser`() {
        assertEquals(listOf("browser"), browserPackages("system.resolver", listOf("browser" to true)))
    }
    @Test
    fun `preferred browser must support the actual web action`() {
        assertEquals(listOf("browser.two"), browserPackages("browser.two", listOf("browser.one" to true, "browser.two" to true)))
        assertEquals(listOf("browser.one"), browserPackages("launcher.only", listOf("launcher.only" to false, "browser.one" to true)))
    }
    @Test
    fun `site apps are excluded and multiple browsers remain available for choosing`() {
        assertEquals(listOf("one", "two"), browserPackages("site.app", listOf("site.app" to false, "one" to true, "two" to true, "one" to true)))
    }
    @Test
    fun `unknown or unavailable browser uses system resolution`() {
        assertEquals(emptyList<String>(), browserPackages("disabled", emptyList()))
    }
}
