package nl.zekerzichtbaar.narrowcasting.ui

import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import nl.zekerzichtbaar.narrowcasting.R
import nl.zekerzichtbaar.narrowcasting.setup.BluetoothAccess
import nl.zekerzichtbaar.narrowcasting.setup.BluetoothProblem
import nl.zekerzichtbaar.narrowcasting.setup.Phase
import nl.zekerzichtbaar.narrowcasting.setup.SetupClient

/** The whole setup flow for one screen. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SetupFlow(client: SetupClient, rescanQr: () -> Unit, close: () -> Unit) {
    val phase by client.phase.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val fix = rememberBluetoothFixes(client, client.link.deviceId, client.usesBluetooth, client::start)

    Scaffold(
        containerColor = Palette.background,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("Scherm ${client.link.deviceId}", fontSize = 17.sp, fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    TextButton(onClick = close, modifier = Modifier.testTag("button.close")) {
                        Text(if (phase.isFinished) "Klaar" else "Stop", color = Palette.accent, fontSize = 16.sp)
                    }
                },
                actions = {
                    if (phase == Phase.Choosing) {
                        if (scanning) {
                            CircularProgressIndicator(
                                color = Palette.muted,
                                strokeWidth = 2.dp,
                                modifier = Modifier.padding(horizontal = 14.dp).size(20.dp),
                            )
                        } else {
                            IconButton(onClick = client::rescan, modifier = Modifier.testTag("button.rescan")) {
                                Icon(painterResource(R.drawable.ic_refresh), "Vernieuwen", tint = Palette.accent)
                            }
                        }
                    }
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = Palette.background,
                    titleContentColor = Palette.text,
                ),
            )
        },
    ) { padding ->
        AnimatedContent(
            targetState = phase,
            contentKey = { it::class },
            transitionSpec = { fadeIn(tween(250)) togetherWith fadeOut(tween(250)) },
            modifier = Modifier.padding(padding).fillMaxSize(),
            label = "phase",
        ) { current ->
            PhaseContent(current, client, rescanQr, fix)
        }
    }
}

@Composable
private fun PhaseContent(phase: Phase, client: SetupClient, rescanQr: () -> Unit, fix: (BluetoothProblem) -> Unit) {
    when (phase) {
        is Phase.Searching -> StepView(
            icon = R.drawable.ic_bluetooth_searching,
            busy = !phase.slow,
            title = if (phase.slow) "Scherm nog niet gevonden" else "Scherm zoeken…",
            message = if (phase.slow) {
                "Ga dichter bij het scherm staan en controleer of het \"Wifi instellen\" toont. We blijven zoeken."
            } else {
                "Houd je telefoon in de buurt van het scherm."
            },
            modifier = Modifier.testTag("step.searching"),
        )
        Phase.Connecting -> StepView(
            icon = R.drawable.ic_bluetooth_connected,
            busy = true,
            title = "Verbinden met het scherm…",
            message = null,
            modifier = Modifier.testTag("step.connecting"),
        )
        is Phase.Bluetooth -> BluetoothProblemView(phase.problem, fix)
        Phase.Choosing -> NetworkList(client)
        is Phase.Working -> StepView(
            icon = R.drawable.ic_wifi,
            busy = true,
            title = phase.message,
            message = "Dit duurt meestal een halve minuut. Laat deze app open.",
            modifier = Modifier.testTag("step.working"),
        )
        is Phase.Failed -> StepView(
            icon = R.drawable.ic_signal_wifi_bad,
            tint = Palette.error,
            title = "Dat lukte niet",
            message = phase.message,
            modifier = Modifier.testTag("step.failed"),
        ) {
            PrimaryButton("Opnieuw proberen", onClick = client::backToList, modifier = Modifier.testTag("button.retry"))
        }
        Phase.Expired -> StepView(
            icon = R.drawable.ic_qr_code_scanner,
            tint = Palette.error,
            title = "QR-code verlopen",
            message = "QR-code verlopen, scan de code op het scherm opnieuw.",
            modifier = Modifier.testTag("step.expired"),
        ) {
            PrimaryButton("QR-code scannen", onClick = rescanQr)
        }
        is Phase.Online -> OnlineView(phase)
        is Phase.Paired -> StepView(
            icon = R.drawable.ic_check_circle,
            title = "Scherm is gekoppeld",
            message = phase.ssid?.let { "Het scherm is verbonden met $it en klaar voor gebruik. Je kunt deze app sluiten." }
                ?: "Het scherm is online en klaar voor gebruik. Je kunt deze app sluiten.",
            modifier = Modifier.testTag("step.paired"),
        )
    }
}

@Composable
internal fun BluetoothProblemView(problem: BluetoothProblem, fix: (BluetoothProblem) -> Unit, managing: Boolean = false) {
    when (problem) {
        BluetoothProblem.OFF -> StepView(
            icon = R.drawable.ic_bluetooth_disabled,
            tint = Palette.error,
            title = "Bluetooth staat uit",
            message = if (managing) {
                "Zet Bluetooth aan. Het scherm wordt via Bluetooth beheerd."
            } else {
                "Zet Bluetooth aan. Het scherm wordt via Bluetooth ingesteld."
            },
            modifier = Modifier.testTag("step.bluetoothOff"),
        ) {
            PrimaryButton("Zet Bluetooth aan", onClick = { fix(problem) })
        }
        BluetoothProblem.UNAUTHORIZED -> StepView(
            icon = R.drawable.ic_shield_lock,
            tint = Palette.error,
            title = "Geen toegang tot Bluetooth",
            message = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                "Sta \"Apparaten in de buurt\" toe om het scherm te kunnen vinden."
            } else {
                "Sta locatie toe om het scherm te kunnen vinden. Android vraagt dat voor Bluetooth; de app gebruikt je locatie niet."
            },
            modifier = Modifier.testTag("step.bluetoothUnauthorized"),
        ) {
            PrimaryButton("Toestaan", onClick = { fix(problem) })
        }
        BluetoothProblem.LOCATION_OFF -> StepView(
            icon = R.drawable.ic_location_off,
            tint = Palette.error,
            title = "Locatie staat uit",
            message = "Op deze Android-versie vindt de telefoon Bluetooth-apparaten alleen met Locatie aan. De app gebruikt je locatie niet.",
            modifier = Modifier.testTag("step.locationOff"),
        ) {
            PrimaryButton("Zet Locatie aan", onClick = { fix(problem) })
        }
        BluetoothProblem.UNSUPPORTED -> StepView(
            icon = R.drawable.ic_warning,
            tint = Palette.error,
            title = "Bluetooth niet beschikbaar",
            message = if (managing) {
                "Deze telefoon ondersteunt geen Bluetooth LE, en dat is nodig om het scherm te beheren."
            } else {
                "Deze telefoon ondersteunt geen Bluetooth LE. Gebruik het instelnetwerk dat het scherm toont."
            },
        )
    }
}

/**
 * Asks for the Bluetooth permissions once when the flow opens, and returns what the problem
 * screens' buttons do. Every return to the app re-checks, as the user may have changed a setting.
 */
@Composable
internal fun rememberBluetoothFixes(
    key: Any,
    deviceId: String,
    usesBluetooth: Boolean,
    recheck: () -> Unit,
): (BluetoothProblem) -> Unit {
    val activity = LocalActivity.current
    var asked by rememberSaveable(deviceId) { mutableStateOf(false) }
    val permissions = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        asked = true
        recheck()
    }
    val enable = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { recheck() }

    LaunchedEffect(key) {
        if (usesBluetooth && !asked && activity != null && !BluetoothAccess.granted(activity)) {
            permissions.launch(BluetoothAccess.permissions)
        }
    }
    LifecycleResumeEffect(key) {
        recheck()
        onPauseOrDispose {}
    }

    return { problem ->
        when (problem) {
            BluetoothProblem.UNAUTHORIZED -> {
                val canAsk = !asked || BluetoothAccess.permissions.any { p ->
                    activity?.shouldShowRequestPermissionRationale(p) == true
                }
                if (canAsk) permissions.launch(BluetoothAccess.permissions) else activity?.openSettings(appDetails = true)
            }
            BluetoothProblem.OFF -> try {
                enable.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            } catch (e: ActivityNotFoundException) {
                activity?.startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
            } catch (e: SecurityException) {
                activity?.openSettings(appDetails = true)
            }
            BluetoothProblem.LOCATION_OFF -> activity?.openSettings(appDetails = false)
            BluetoothProblem.UNSUPPORTED -> Unit
        }
    }
}

private fun Activity.openSettings(appDetails: Boolean) {
    val intent = if (appDetails) {
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
    } else {
        Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)
    }
    try {
        startActivity(intent)
    } catch (_: ActivityNotFoundException) {
        startActivity(Intent(Settings.ACTION_SETTINGS))
    }
}

@Composable
private fun OnlineView(phase: Phase.Online) {
    val uriHandler = LocalUriHandler.current
    StepView(
        icon = R.drawable.ic_check_circle,
        title = "Het scherm is online",
        message = phase.ssid?.let { "Verbonden met $it." },
        modifier = Modifier.testTag("step.online"),
    ) {
        Column(
            Modifier.fillMaxWidth().background(Palette.card, RoundedCornerShape(16.dp)).padding(vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "KOPPELCODE",
                color = Palette.muted,
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Medium,
                letterSpacing = 1.5.sp,
            )
            val code = phase.code
            if (code != null) {
                SelectionContainer {
                    Text(
                        code,
                        color = Palette.text,
                        fontSize = 44.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.testTag("label.code"),
                    )
                }
            } else {
                Row(
                    Modifier.height(52.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    CircularProgressIndicator(color = Palette.muted, strokeWidth = 2.dp, modifier = Modifier.size(20.dp))
                    Text("Code ophalen…", color = Palette.muted, fontSize = 16.sp)
                }
            }
        }
        phase.url?.let { url ->
            PrimaryButton(
                "Koppel dit scherm",
                onClick = { runCatching { uriHandler.openUri(url) } },
                modifier = Modifier.testTag("button.pair"),
            )
        }
        Text(
            "Koppel het scherm in Narrowcasting met deze code. Het scherm toont dezelfde code.",
            color = Palette.muted,
            fontSize = 13.sp,
            textAlign = TextAlign.Center,
        )
    }
}
