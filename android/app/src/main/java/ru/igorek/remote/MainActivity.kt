package ru.igorek.remote

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.service.BridgeConnectionService
import ru.igorek.remote.service.VoiceCalls
import ru.igorek.remote.service.VoiceCallService
import ru.igorek.remote.ui.VoiceCallHost
import ru.igorek.remote.ui.AppRoot
import ru.igorek.remote.ui.IgorekTheme

class MainActivity : ComponentActivity() {

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Bridge.init(applicationContext)
        applyScreenshotProtection(Bridge.screenshotsBlocked.value)
        askForNotificationPermissionIfNeeded()
        setContent {
            IgorekTheme {
                val screenshotsBlocked by Bridge.screenshotsBlocked.collectAsState()
                SideEffect { applyScreenshotProtection(screenshotsBlocked) }
                val paired by Bridge.paired.collectAsState()
                LaunchedEffect(paired) {
                    if (paired) startBridgeService()
                    else stopService(Intent(this@MainActivity, BridgeConnectionService::class.java))
                }
                AppRoot()
                VoiceCallHost()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        VoiceCalls.activityVisible = true
        Bridge.setActivityVisible(true)
        if (intent?.action == VoiceCallService.OPEN) VoiceCalls.show()
    }

    override fun onStop() {
        VoiceCalls.activityVisible = false
        Bridge.setActivityVisible(false)
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (intent.action == VoiceCallService.OPEN) VoiceCalls.show()
    }

    private fun applyScreenshotProtection(blocked: Boolean) {
        if (blocked) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
    }

    private fun askForNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun startBridgeService() {
        ContextCompat.startForegroundService(this, Intent(this, BridgeConnectionService::class.java))
    }
}
