package nl.zekerzichtbaar.narrowcasting

import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Walks the flow against the simulated Pi: wrong password, retry, online. Each step is also saved
 * as a PNG in the app's external files dir (`screenshots/`).
 */
@OptIn(ExperimentalTestApi::class)
@RunWith(AndroidJUnit4::class)
class SetupFlowTest {
    @get:Rule val compose = createEmptyComposeRule()

    private var scenario: ActivityScenario<MainActivity>? = null
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun launch(intent: Intent) {
        scenario = ActivityScenario.launch(intent.setClass(instrumentation.targetContext, MainActivity::class.java))
    }

    @After fun close() {
        scenario?.close()
    }

    private fun shot(name: String) {
        compose.waitForIdle()
        val dir = File(instrumentation.targetContext.getExternalFilesDir(null), "screenshots").apply { mkdirs() }
        instrumentation.uiAutomation.takeScreenshot()?.let { bitmap ->
            File(dir, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        }
    }

    private fun waitForTag(tag: String, seconds: Long = 10) = compose.waitUntilAtLeastOneExists(hasTestTag(tag), seconds * 1000)

    // Inside a text field: the field merges its children's semantics.
    private fun waitForInnerTag(tag: String, seconds: Long) = compose.waitUntil(seconds * 1000) {
        compose.onAllNodes(hasTestTag(tag), useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    }

    @Test fun wrongPasswordThenOnline() {
        launch(Intent().putExtra(MainActivity.EXTRA_MOCK, true))
        shot("00-searching")
        waitForTag("network.Kantine")
        shot("01-list")

        compose.onNodeWithTag("network.Kantine").performClick()
        waitForTag("field.password", 5)
        compose.onNodeWithTag("field.password").performTextInput("kort")
        compose.onNodeWithTag("button.connect").performClick()
        waitForInnerTag("label.problem", 3)
        shot("02-password-too-short")
        compose.onNodeWithTag("field.password").performTextClearance()
        compose.onNodeWithTag("field.password").performTextInput("fout1234")
        compose.onNodeWithTag("button.connect").performClick()

        waitForTag("step.working", 5)
        shot("03-connecting")
        waitForTag("button.retry")
        shot("04-failed")
        compose.waitUntilAtLeastOneExists(hasText("Verkeerd wachtwoord?", substring = true), 1000)

        compose.onNodeWithTag("button.retry").performClick()
        waitForTag("network.Kantine", 5)
        compose.onNodeWithTag("network.Kantine").performClick()
        waitForTag("field.password", 5)
        compose.onNodeWithTag("field.password").performTextInput("geheim123")
        compose.onNodeWithTag("field.password").performImeAction()

        waitForTag("step.online")
        shot("05-online-waiting-for-code")
        compose.waitUntilAtLeastOneExists(hasText("K7Q-4M2"), 10_000)
        compose.onNodeWithTag("button.pair").assertExists()
        shot("06-online-code")
    }

    @Test fun rescanAndOtherNetwork() {
        launch(Intent().putExtra(MainActivity.EXTRA_MOCK, true))
        waitForTag("network.Kantine")
        compose.onNodeWithTag("button.rescan").performClick()
        Thread.sleep(1000)
        shot("07-rescanning")
        waitForTag("network.Buren")
        shot("08-rescanned")

        compose.onNodeWithTag("network.other").performClick()
        waitForTag("field.ssid", 5)
        compose.onNodeWithTag("field.ssid").performTextInput("Verstopt")
        shot("09-other-network")
    }

    @Test fun demoLinkOpensTheSimulatedScreen() {
        launch(Intent(Intent.ACTION_VIEW, Uri.parse("https://app.zekerzichtbaar.tv/setup?d=DEMO&k=AAECAwQFBgcICQoLDA0ODw")))
        compose.waitUntilAtLeastOneExists(hasText("Scherm DEMO"), 5000)
        waitForTag("network.Kantine")
    }

    @Test fun homeRejectsALinkThatIsNotAScreen() {
        launch(Intent())
        waitForTag("step.home", 5)
        shot("10-home")
        compose.onNodeWithTag("button.typeLink").performClick()
        compose.onNodeWithTag("field.link").performTextInput("https://example.com/")
        compose.onNodeWithTag("button.openLink").performClick()
        compose.waitUntilAtLeastOneExists(hasText("Dit is geen link van een scherm."), 3000)
        shot("11-wrong-link")
        compose.onNodeWithTag("field.link").performTextClearance()
        compose.onNodeWithTag("field.link").performTextInput("https://app.zekerzichtbaar.tv/setup?d=DEMO&k=AAECAwQFBgcICQoLDA0ODw")
        compose.onNodeWithTag("button.openLink").performClick()
        waitForTag("network.Kantine")
    }
}
