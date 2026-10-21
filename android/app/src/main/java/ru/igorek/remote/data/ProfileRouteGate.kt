package ru.igorek.remote.data

internal class ProfileRouteGate {
    private var sends = 0
    private var switching = false

    @Synchronized fun beginSend(): Boolean {
        if (switching) return false
        sends++
        return true
    }

    @Synchronized fun endSend() { check(sends > 0); sends-- }

    @Synchronized fun beginSwitch(): Boolean {
        if (switching || sends > 0) return false
        switching = true
        return true
    }

    @Synchronized fun endSwitch() { switching = false }
}
