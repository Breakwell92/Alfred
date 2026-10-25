package com.alfred.app.ui.transmitting

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import com.alfred.app.data.voice.SpokenWord
import com.alfred.app.ui.theme.AlfredDarkColors
import org.junit.Assert.assertEquals
import org.junit.Test

class ReplyHighlightTest {

    private val colors = AlfredDarkColors
    private val reply = "It's  7:30, sir.\nShall I wake you?"
    private val words = listOf(
        SpokenWord("It's", 0, 200),
        SpokenWord("7:30,", 250, 900, emphasis = true),
        SpokenWord("sir.", 950, 1_200),
        SpokenWord("Shall", 1_500, 1_700),
        SpokenWord("I", 1_700, 1_800),
        SpokenWord("wake", 1_800, 2_000),
        SpokenWord("you?", 2_000, 2_300),
    )

    /** The colour of the last span covering [index]: later styles win in Compose. */
    private fun AnnotatedString.colourAt(index: Int): Color? =
        spanStyles.lastOrNull { index >= it.start && index < it.end }?.item?.color

    private fun AnnotatedString.colourOf(word: String): Color? = colourAt(text.indexOf(word))

    @Test
    fun `words are found in order in the reply text`() {
        val spans = alignWords(reply, words)

        assertEquals(words, spans.map { it.word })
        spans.forEach { assertEquals(it.word.word, reply.substring(it.start, it.end)) }
    }

    @Test
    fun `a word missing from the text is skipped, not the rest`() {
        val spans = alignWords("Hello there sir", listOf(SpokenWord("Hello", 0, 1), SpokenWord("nope", 1, 2), SpokenWord("sir", 2, 3)))

        assertEquals(listOf("Hello", "sir"), spans.map { it.word.word })
    }

    @Test
    fun `current word is the last one started`() {
        val spans = alignWords(reply, words)

        assertEquals(-1, currentWord(spans, -1))
        assertEquals(0, currentWord(spans, 0))
        assertEquals(0, currentWord(spans, 230)) // in the gap after "It's"
        assertEquals(1, currentWord(spans, 250))
        assertEquals(6, currentWord(spans, 10_000))
        assertEquals(-1, currentWord(emptyList(), 500))
    }

    @Test
    fun `following the audio, heard words are bright, the current one accent, the rest dim`() {
        val spans = alignWords(reply, words)

        val text = replyText(reply, spans, 1_000, Highlight.FollowAudio, colors)

        assertEquals(reply, text.text)
        assertEquals(colors.textPrimary, text.colourOf("It's"))
        assertEquals("a heard value stays accent", colors.accent, text.colourOf("7:30"))
        assertEquals(colors.accent, text.colourOf("sir."))
        assertEquals(colors.textDim, text.colourOf("Shall"))
        assertEquals(colors.textDim, text.colourOf("you?"))
    }

    @Test
    fun `nothing heard yet is all dim`() {
        val spans = alignWords(reply, words)

        val text = replyText(reply, spans, -1, Highlight.FollowAudio, colors)

        assertEquals(colors.textDim, text.colourOf("It's"))
        assertEquals(colors.textDim, text.colourOf("you?"))
    }

    @Test
    fun `plain shows everything at once with values in accent`() {
        val spans = alignWords(reply, words)

        val text = replyText(reply, spans, 0, Highlight.Plain, colors)

        assertEquals(colors.textPrimary, text.colourOf("Shall"))
        assertEquals(colors.accent, text.colourOf("7:30"))
        assertEquals(colors.textPrimary, replyText("No timings.", emptyList(), 0, Highlight.Plain, colors).colourAt(0))
    }

    @Test
    fun `which highlight`() {
        assertEquals(Highlight.Pending, highlightFor(speaking = false, thinking = true, hasWords = false))
        assertEquals(Highlight.FollowAudio, highlightFor(speaking = false, thinking = true, hasWords = true))
        assertEquals(Highlight.FollowAudio, highlightFor(speaking = true, thinking = false, hasWords = true))
        // No timings from the server: show it all as it arrives.
        assertEquals(Highlight.Plain, highlightFor(speaking = true, thinking = false, hasWords = false))
        // Finished: all bright.
        assertEquals(Highlight.Plain, highlightFor(speaking = false, thinking = false, hasWords = true))
    }
}
