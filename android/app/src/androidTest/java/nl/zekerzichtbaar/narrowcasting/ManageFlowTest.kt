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
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

private const val DEMO_STICKER = "https://app.zekerzichtbaar.tv/setup?d=DEMO&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"

/**
 * Manages the simulated paired Pi from a sticker: status, wrong password with fallback, switch.
 * Each step is saved as a PNG in the app's external files dir (`screenshots/`).
 */
@OptIn(ExperimentalTestApi::class)
@RunWith(AndroidJUnit4::class)
class ManageFlowTest {
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

    private fun waitForTag(tag: String, seconds: Long = 15) = compose.waitUntilAtLeastOneExists(hasTestTag(tag), seconds * 1000)

    private fun waitForText(text: String, seconds: Long = 10) =
        compose.waitUntilAtLeastOneExists(hasText(text, substring = true), seconds * 1000)

    private fun choose(network: String, password: String, submitWithKeyboard: Boolean = false) {
        waitForTag("network.$network", 10)
        compose.onNodeWithTag("network.$network").performClick()
        waitForTag("field.password", 5)
        compose.onNodeWithTag("field.password").performTextInput(password)
        if (submitWithKeyboard) {
            compose.onNodeWithTag("field.password").performImeAction()
        } else {
            compose.onNodeWithTag("button.connect").performClick()
        }
    }

    @Test fun statusThenWrongPasswordThenSwitch() {
        launch(Intent().putExtra(MainActivity.EXTRA_MOCK_MANAGE, true))
        shot("m00-searching")
        waitForTag("step.status")
        waitForText("Kantine links")
        waitForText("Kantine · 82%")
        waitForText("192.168.1.42")
        waitForText("v0.9.7")
        shot("m01-status")

        compose.onNodeWithTag("button.changeWifi").performClick()
        waitForTag("step.choosing", 5)
        shot("m02-scanning")
        waitForTag("network.Office 5G")
        shot("m03-list")

        choose("Office 5G", "fout1234")
        waitForTag("step.working", 5)
        shot("m04-working")
        waitForTag("step.failed")
        waitForText("Terug naar Kantine.")
        waitForText("Naar status (Kantine)")
        shot("m05-failed-fallback")

        compose.onNodeWithTag("button.retry").performClick()
        choose("Office 5G", "geheim123", submitWithKeyboard = true)
        waitForTag("step.joined")
        waitForText("Verbonden met Office 5G")
        shot("m06-joined")

        compose.onNodeWithTag("button.done").performClick()
        waitForTag("step.status", 5)
        waitForText("Office 5G · 74%")
        waitForText("192.168.1.57")
        shot("m07-status-switched")
    }

    @Test fun demoStickerOpensManagement() {
        launch(Intent(Intent.ACTION_VIEW, Uri.parse(DEMO_STICKER)))
        waitForText("Scherm DEMO", 5)
        waitForTag("step.status")
        waitForText("Kantine links")
        shot("m08-demo-sticker")
    }

    @Test fun homeOpensATypedStickerLink() {
        launch(Intent())
        waitForTag("step.home", 5)
        shot("m09-home")
        compose.onNodeWithTag("button.typeLink").performClick()
        compose.onNodeWithTag("field.link").performTextInput(DEMO_STICKER)
        compose.onNodeWithTag("button.openLink").performClick()
        waitForTag("step.status")
    }

    @Test fun aBrokenTokenIsNotASticker() {
        launch(Intent(Intent.ACTION_VIEW, Uri.parse("$DEMO_STICKER&k=kapot")))
        waitForTag("step.home", 5)
        waitForText("Deze link hoort niet bij een scherm.")
    }
}
