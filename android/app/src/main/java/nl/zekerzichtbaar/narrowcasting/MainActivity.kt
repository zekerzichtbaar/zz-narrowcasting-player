package nl.zekerzichtbaar.narrowcasting

import android.app.Application
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import nl.zekerzichtbaar.narrowcasting.setup.BleTransport
import nl.zekerzichtbaar.narrowcasting.setup.MockTransport
import nl.zekerzichtbaar.narrowcasting.setup.SetupClient
import nl.zekerzichtbaar.narrowcasting.setup.SetupLink
import nl.zekerzichtbaar.narrowcasting.ui.HomeScreen
import nl.zekerzichtbaar.narrowcasting.ui.NarrowcastingTheme
import nl.zekerzichtbaar.narrowcasting.ui.SetupFlow

/**
 * Opened from the launcher, or by the TV's QR code (App Link `https://app.zekerzichtbaar.tv/setup`).
 * Launch with the extra `mockSetup=true` to run the flow against a simulated Pi.
 */
class MainActivity : ComponentActivity() {
    private val model: SetupModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null && intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY == 0) {
            handle(intent)
        }
        setContent {
            NarrowcastingTheme { App(model) }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(intent: Intent) {
        if (intent.getBooleanExtra(EXTRA_MOCK, false)) {
            model.open(SetupLink.parse(MOCK_URL)!!, mock = true)
            return
        }
        val url = intent.dataString ?: return
        val link = SetupLink.parse(url)
        if (link != null) model.open(link) else model.unknownLink = model.client == null
    }

    companion object {
        const val EXTRA_MOCK = "mockSetup"
        const val MOCK_URL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
    }
}

/** Owns the SetupClient for a link; a new link (another QR) starts over. */
class SetupModel(app: Application) : AndroidViewModel(app) {
    var client by mutableStateOf<SetupClient?>(null)
        private set
    var unknownLink by mutableStateOf(false)

    fun open(link: SetupLink, mock: Boolean = false) {
        unknownLink = false
        if (link == client?.link) return
        client?.stop()
        val transport = if (mock || link.isDemo) MockTransport(viewModelScope) else BleTransport(getApplication())
        client = SetupClient(link, transport, viewModelScope).also { it.start() }
    }

    fun close() {
        client?.stop()
        client = null
    }

    override fun onCleared() {
        client?.stop()
    }
}

@Composable
private fun App(model: SetupModel) {
    val client = model.client
    if (client != null) {
        BackHandler(onBack = model::close)
        SetupFlow(client, rescanQr = model::close, close = model::close)
    } else {
        HomeScreen(
            message = if (model.unknownLink) {
                "Deze link hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont."
            } else {
                "Scan de QR-code die het scherm bij \"Wifi instellen\" toont. Daarna stel je de wifi van het scherm in via Bluetooth."
            },
            open = model::open,
        )
    }
}
