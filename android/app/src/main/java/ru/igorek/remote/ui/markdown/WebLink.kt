package ru.igorek.remote.ui.markdown

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.widget.Toast
import ru.igorek.remote.data.AppLocale

internal fun browserPackages(preferred: String?, handlers: List<Pair<String, Boolean>>): List<String> {
    val browsers = handlers.filter { it.second }.map { it.first }.distinct()
    return if (preferred != null && preferred in browsers) listOf(preferred) else browsers
}

internal fun openWebLink(context: Context, raw: String) {
    val url = MarkdownLinks.webUrl(raw) ?: return
    val view = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
        addCategory(Intent.CATEGORY_BROWSABLE)
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    val manager = context.packageManager
    val preferred = manager.resolveActivity(
        Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, Intent.CATEGORY_APP_BROWSER), 0,
    )?.activityInfo?.packageName
    val handlers = manager.queryIntentActivities(view, PackageManager.MATCH_DEFAULT_ONLY or PackageManager.GET_RESOLVED_FILTER)
        .map { info ->
            val filter = info.filter
            val allHosts = filter != null && (filter.countDataAuthorities() == 0 ||
                (0 until filter.countDataAuthorities()).any { filter.getDataAuthority(it).host == "*" })
            info.activityInfo.packageName to allHosts
        }
    val packages = browserPackages(preferred, handlers)
    val selected = when (packages.size) {
        0 -> view
        1 -> Intent(view).setPackage(packages.single())
        else -> Intent.createChooser(Intent(view).setPackage(packages.first()),
            AppLocale.text("Открыть в браузере", "Open in browser")).apply {
            putExtra(Intent.EXTRA_INITIAL_INTENTS, packages.drop(1).map { Intent(view).setPackage(it) }.toTypedArray())
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }
    fun launch(intent: Intent): Boolean = try {
        context.startActivity(intent)
        true
    } catch (_: ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }
    if (launch(selected) || selected !== view && launch(view)) return
    Toast.makeText(context, AppLocale.text("Не найден браузер для этой ссылки", "No browser found for this link"), Toast.LENGTH_SHORT).show()
}
