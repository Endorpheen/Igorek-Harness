package ru.igorek.remote

import android.os.Bundle
import androidx.activity.ComponentActivity
import ru.igorek.remote.data.Bridge

/** Non-exported debug host for production composables under instrumentation. */
class UiAcceptanceActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Bridge.init(applicationContext)
    }

    override fun onStart() {
        super.onStart()
        Bridge.setActivityVisible(true)
    }

    override fun onStop() {
        Bridge.setActivityVisible(false)
        super.onStop()
    }
}
