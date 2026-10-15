package ru.igorek.remote.ui

import android.os.Bundle
import android.view.WindowManager
import com.journeyapps.barcodescanner.CaptureActivity
import ru.igorek.remote.data.Bridge

class QrCaptureActivity : CaptureActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        Bridge.init(applicationContext)
        protectScreen()
        super.onCreate(savedInstanceState)
    }

    override fun onResume() {
        protectScreen()
        super.onResume()
    }

    private fun protectScreen() {
        if (Bridge.screenshotsBlocked.value) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }
}
