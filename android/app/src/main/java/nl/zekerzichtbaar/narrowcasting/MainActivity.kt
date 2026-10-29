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
import nl.zekerzichtbaar.narrowcasting.setup.ManageBleTransport
import nl.zekerzichtbaar.narrowcasting.setup.ManageClient
import nl.zekerzichtbaar.narrowcasting.setup.ManageLink
import nl.zekerzichtbaar.narrowcasting.setup.MockManageTransport
import nl.zekerzichtbaar.narrowcasting.setup.MockTransport
import nl.zekerzichtbaar.narrowcasting.setup.ScreenLink
import nl.zekerzichtbaar.narrowcasting.setup.SetupClient
import nl.zekerzichtbaar.narrowcasting.setup.SetupLink
import nl.zekerzichtbaar.narrowcasting.ui.HomeScreen
import nl.zekerzichtbaar.narrowcasting.ui.ManageFlow
import nl.zekerzichtbaar.narrowcasting.ui.NarrowcastingTheme
import nl.zekerzichtbaar.narrowcasting.ui.SetupFlow

/**
 * Opened from the launcher, by the TV's QR code or by the sticker on the player (App Link
 * `https://app.zekerzichtbaar.tv/setup`). Launch with the extra `mockSetup=true` to run the setup flow
 * against a simulated Pi, or `mockManage=true` to manage a simulated paired Pi.
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
            model.open(ScreenLink.Setup(SetupLink.parse(MOCK_URL)!!), mock = true)
            return
        }
        if (intent.getBooleanExtra(EXTRA_MOCK_MANAGE, false)) {
            model.open(ScreenLink.Manage(ManageLink.parse(MOCK_MANAGE_URL)!!), mock = true)
            return
        }
        val url = intent.dataString ?: return
        val link = ScreenLink.parse(url)
        if (link != null) model.open(link) else model.unknownLink = model.client == null && model.manageClient == null
    }

    companion object {
        const val EXTRA_MOCK = "mockSetup"
        const val EXTRA_MOCK_MANAGE = "mockManage"
        const val MOCK_URL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
        const val MOCK_MANAGE_URL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
    }
}

/** Owns the client for a link (setup for a TV QR, management for a sticker); a new link starts over. */
class SetupModel(app: Application) : AndroidViewModel(app) {
    var client by mutableStateOf<SetupClient?>(null)
        private set
    var manageClient by mutableStateOf<ManageClient?>(null)
        private set
    var unknownLink by mutableStateOf(false)

    fun open(link: ScreenLink, mock: Boolean = false) {
        unknownLink = false
        when (link) {
            is ScreenLink.Setup -> {
                val setup = link.link
                if (setup == client?.link) return
                close()
                val transport = if (mock || setup.isDemo) MockTransport(viewModelScope) else BleTransport(getApplication())
                client = SetupClient(setup, transport, viewModelScope).also { it.start() }
            }
            is ScreenLink.Manage -> {
                val manage = link.link
                if (manage == manageClient?.link) return
                close()
                val transport = if (mock || manage.isDemo) {
                    MockManageTransport.demo(manage, viewModelScope)
                } else {
                    ManageBleTransport(getApplication())
                }
                manageClient = ManageClient(manage, transport, viewModelScope).also { it.start() }
            }
        }
    }

    fun close() {
        client?.stop()
        client = null
        manageClient?.stop()
        manageClient = null
    }

    override fun onCleared() {
        client?.stop()
        manageClient?.stop()
    }
}

@Composable
private fun App(model: SetupModel) {
    val client = model.client
    val manageClient = model.manageClient
    if (client != null) {
        BackHandler(onBack = model::close)
        SetupFlow(client, rescanQr = model::close, close = model::close)
    } else if (manageClient != null) {
        BackHandler(onBack = model::close)
        ManageFlow(manageClient, rescanQr = model::close, close = model::close)
    } else {
        HomeScreen(
            message = if (model.unknownLink) {
                "Deze link hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont, of de QR-sticker op de speler."
            } else {
                "Scan de QR-code die het scherm bij \"Wifi instellen\" toont, of de QR-sticker op de speler. Daarna stel je de wifi van het scherm in via Bluetooth."
            },
            open = { model.open(it) },
        )
    }
}
