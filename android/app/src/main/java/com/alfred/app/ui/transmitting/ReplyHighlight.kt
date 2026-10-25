package com.alfred.app.ui.transmitting

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import com.alfred.app.data.voice.SpokenWord
import com.alfred.app.ui.theme.AlfredColors

/** Where a timed word sits in the reply text: characters [start] until [end]. */
data class WordSpan(val start: Int, val end: Int, val word: SpokenWord)

/** How the reply is coloured (ADR 0023). */
enum class Highlight {
    /** Not spoken yet (Alfred is still thinking): all dim. */
    Pending,

    /** Karaoke: heard words bright, the word being spoken in accent, the rest dim. */
    FollowAudio,

    /** No timings, or the reply has finished: all bright, values in accent. */
    Plain,
}

/**
 * Finds each timed word in [reply], in order. The server's words are the reply's own
 * whitespace-separated tokens, so each is found at or after the previous one; one that isn't
 * (it shouldn't happen) is skipped rather than throwing the rest off.
 */
fun alignWords(reply: String, words: List<SpokenWord>): List<WordSpan> {
    val spans = ArrayList<WordSpan>(words.size)
    var cursor = 0
    for (word in words) {
        val at = reply.indexOf(word.word, cursor)
        if (at < 0) continue
        spans += WordSpan(at, at + word.word.length, word)
        cursor = at + word.word.length
    }
    return spans
}

/** The span being spoken at [positionMillis]: the last one that has started; -1 before the first. */
fun currentWord(spans: List<WordSpan>, positionMillis: Long): Int {
    var low = 0
    var high = spans.size - 1
    var found = -1
    while (low <= high) {
        val mid = (low + high) ushr 1
        if (spans[mid].word.startMillis <= positionMillis) {
            found = mid
            low = mid + 1
        } else {
            high = mid - 1
        }
    }
    return found
}

/** [Highlight.FollowAudio] once timings arrive; dim before anything's spoken; plain otherwise. */
fun highlightFor(speaking: Boolean, thinking: Boolean, hasWords: Boolean): Highlight = when {
    hasWords && (speaking || thinking) -> Highlight.FollowAudio
    thinking -> Highlight.Pending
    else -> Highlight.Plain
}

/** The reply coloured for [highlight], with [positionMillis] of it heard. */
fun replyText(
    reply: String,
    spans: List<WordSpan>,
    positionMillis: Long,
    highlight: Highlight,
    colors: AlfredColors,
): AnnotatedString = buildAnnotatedString {
    append(reply)
    val bright = SpanStyle(color = colors.textPrimary)
    val dim = SpanStyle(color = colors.textDim)
    val accent = SpanStyle(color = colors.accent)
    when (highlight) {
        Highlight.Pending -> addStyle(dim, 0, reply.length)
        Highlight.Plain -> {
            addStyle(bright, 0, reply.length)
            spans.filter { it.word.emphasis }.forEach { addStyle(accent, it.start, it.end) }
        }
        Highlight.FollowAudio -> {
            val current = currentWord(spans, positionMillis)
            if (current < 0) {
                addStyle(dim, 0, reply.length)
                return@buildAnnotatedString
            }
            val now = spans[current]
            addStyle(bright, 0, now.start)
            for (i in 0 until current) {
                if (spans[i].word.emphasis) addStyle(accent, spans[i].start, spans[i].end)
            }
            addStyle(accent, now.start, now.end)
            addStyle(dim, now.end, reply.length)
        }
    }
}
