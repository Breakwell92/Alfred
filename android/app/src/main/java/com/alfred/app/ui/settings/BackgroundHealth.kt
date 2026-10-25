package com.alfred.app.ui.settings

import com.alfred.app.data.reliability.ServiceHealth
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The health indicator under Settings → Wake word (ADR 0025), e.g. "Listening since 09:12",
 * "Restarted 2× · last at 03:40 after a 12 min gap", "Reconnected 5×".
 */
fun healthLines(health: ServiceHealth, running: Boolean, now: Long, zone: ZoneId = ZoneId.systemDefault()): List<String> =
    buildList {
        val since = health.listeningSince
        if (running && since != null) add("Listening since ${clock(since, now, zone)}")
        add(
            if (health.restartCount == 0) {
                "No restarts after Android stopped it"
            } else {
                val last = health.lastRestartAt?.let { " · last at ${clock(it, now, zone)}" }.orEmpty()
                val gap = health.lastGapMillis?.let { " after a ${duration(it)} gap" }.orEmpty()
                "Restarted ${health.restartCount}×$last$gap"
            },
        )
        add("Reconnected to the server ${health.reconnectCount}×")
    }

/** "09:12" today, "Mon 09:12" within the week, else "3 Oct 09:12". */
private fun clock(at: Long, now: Long, zone: ZoneId): String {
    val time = Instant.ofEpochMilli(at).atZone(zone)
    val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    val pattern = when {
        time.toLocalDate() == today -> "HH:mm"
        time.toLocalDate().isAfter(today.minusDays(7)) -> "EEE HH:mm"
        else -> "d MMM HH:mm"
    }
    return time.format(DateTimeFormatter.ofPattern(pattern, Locale.UK))
}

private fun duration(millis: Long): String {
    val minutes = millis / 60_000
    return when {
        minutes < 1 -> "under a minute"
        minutes < 60 -> "$minutes min"
        minutes % 60 == 0L -> "${minutes / 60} h"
        else -> "${minutes / 60} h ${minutes % 60} min"
    }
}

/** Manufacturers with their own page on dontkillmyapp.com, by `Build.MANUFACTURER` (lower case). */
private val DONT_KILL_MY_APP_PAGES = mapOf(
    "samsung" to "samsung",
    "google" to "google",
    "oneplus" to "oneplus",
    "xiaomi" to "xiaomi",
    "redmi" to "xiaomi",
    "poco" to "xiaomi",
    "huawei" to "huawei",
    "honor" to "huawei",
    "oppo" to "oppo",
    "realme" to "realme",
    "vivo" to "vivo",
    "motorola" to "motorola",
    "nokia" to "nokia",
    "hmd global" to "nokia",
    "sony" to "sony",
    "asus" to "asus",
    "lenovo" to "lenovo",
    "meizu" to "meizu",
)

/** The guide to stopping this phone's maker from killing background apps. */
fun dontKillMyAppUrl(manufacturer: String): String {
    val page = DONT_KILL_MY_APP_PAGES[manufacturer.trim().lowercase(Locale.ROOT)]
    return if (page == null) "https://dontkillmyapp.com/" else "https://dontkillmyapp.com/$page"
}
