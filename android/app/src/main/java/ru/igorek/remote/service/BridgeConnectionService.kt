package ru.igorek.remote.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import ru.igorek.remote.MainActivity
import ru.igorek.remote.R
import ru.igorek.remote.data.Alert
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge

class BridgeConnectionService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var alertsJob: Job? = null
    private var alertCounter = ALERT_ID_START

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Bridge.init(applicationContext)
        createChannels()
        startForegroundCompat()
        Bridge.startStream()
        alertsJob = scope.launch {
            Bridge.alerts.collect { alert -> postAlert(alert) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        alertsJob?.cancel()
        Bridge.stopStream()
        scope.cancel()
        super.onDestroy()
    }

    private fun startForegroundCompat() {
        val notification = buildStatusNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(STATUS_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(STATUS_ID, notification)
        }
    }

    private fun buildStatusNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_STATUS)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(AppLocale.text("Подключение к runtime активно", "Runtime connection is active"))
            .setSmallIcon(R.drawable.ic_launcher)
            .setOngoing(true)
            .setContentIntent(openAppIntent())
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    private fun postAlert(alert: Alert) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ALERTS)
            .setContentTitle(alert.title)
            .setContentText(alert.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.text))
            .setSmallIcon(R.drawable.ic_launcher)
            .setAutoCancel(true)
            .setContentIntent(openAppIntent())
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        try {
            NotificationManagerCompat.from(this).notify(alertCounter++, notification)
        } catch (_: SecurityException) {
            // No permission for notifications has been granted—notification is being skipped.
        }
    }

    private fun openAppIntent(): PendingIntent =
        PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_STATUS,
                AppLocale.text("Соединение с Игорьком", "Igorek connection"),
                NotificationManager.IMPORTANCE_LOW,
            )
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERTS,
                AppLocale.text("События Игорька", "Igorek events"),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = AppLocale.text(
                    "Подтверждения, ошибки и завершение длительных задач",
                    "Approvals, errors, and long-running task completion",
                )
            }
        )
    }

    private companion object {
        const val CHANNEL_STATUS = "igorek_status"
        const val CHANNEL_ALERTS = "igorek_alerts"
        const val STATUS_ID = 1
        const val ALERT_ID_START = 100
    }
}
