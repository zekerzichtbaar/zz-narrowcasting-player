package nl.zekerzichtbaar.narrowcasting.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import nl.zekerzichtbaar.narrowcasting.R
import nl.zekerzichtbaar.narrowcasting.setup.BluetoothProblem
import nl.zekerzichtbaar.narrowcasting.setup.ManageClient
import nl.zekerzichtbaar.narrowcasting.setup.ManagePhase
import nl.zekerzichtbaar.narrowcasting.setup.ManageStatus

/** Status and wifi of a paired screen, opened from the QR sticker on the player. */
@Composable
fun ManageFlow(client: ManageClient, rescanQr: () -> Unit, close: () -> Unit) {
    val phase by client.phase.collectAsStateWithLifecycle()
    val status by client.status.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val refreshing by client.isRefreshing.collectAsStateWithLifecycle()
    val connected by client.isConnected.collectAsStateWithLifecycle()
    val fix = rememberBluetoothFixes(client, client.link.deviceId, client.usesBluetooth, client::recheckBluetooth)

    BackHandler(enabled = phase == ManagePhase.Choosing, onBack = client::backToStatus)

    Column(Modifier.fillMaxSize().background(Palette.background).safeDrawingPadding()) {
        TopBar(
            Modifier.padding(top = 8.dp),
            leading = {
                if (phase == ManagePhase.Choosing) {
                    SecondaryButton(
                        "Status",
                        onClick = client::backToStatus,
                        compact = true,
                        icon = R.drawable.ic_chevron_left,
                        modifier = Modifier.testTag("button.backToStatus"),
                    )
                } else {
                    SecondaryButton("Klaar", onClick = close, compact = true, modifier = Modifier.testTag("button.close"))
                }
            },
            trailing = {
                when (phase) {
                    ManagePhase.Status -> RescanButton(
                        refreshing,
                        onClick = client::refresh,
                        enabled = connected,
                        modifier = Modifier.testTag("button.refresh"),
                    )
                    ManagePhase.Choosing -> RescanButton(scanning, onClick = client::rescan, modifier = Modifier.testTag("button.rescan"))
                    else -> DeviceLabel(status?.name ?: "Scherm ${client.link.deviceId}")
                }
            },
        )
        AnimatedContent(
            targetState = phase,
            contentKey = { it::class },
            transitionSpec = { fadeIn(tween(250)) togetherWith fadeOut(tween(250)) },
            modifier = Modifier.fillMaxSize(),
            label = "managePhase",
        ) { current ->
            ManagePhaseContent(current, client, rescanQr, fix)
        }
    }
}

@Composable
private fun ManagePhaseContent(
    phase: ManagePhase,
    client: ManageClient,
    rescanQr: () -> Unit,
    fix: (BluetoothProblem) -> Unit,
) {
    when (phase) {
        is ManagePhase.Searching -> SearchingView(
            slow = phase.slow,
            title = if (phase.slow) "Scherm nog niet gevonden" else "Scherm zoeken…",
            message = if (phase.slow) {
                "Ga dichter bij de speler staan en controleer of hij aan staat. We blijven zoeken."
            } else {
                "Houd je telefoon in de buurt van de speler."
            },
            modifier = Modifier.testTag("step.searching"),
        )
        ManagePhase.Connecting -> StepView(
            icon = R.drawable.ic_bluetooth_connected,
            busy = true,
            title = "Verbinden met het scherm…",
            message = null,
            modifier = Modifier.testTag("step.connecting"),
        )
        is ManagePhase.Bluetooth -> BluetoothProblemView(phase.problem, fix, managing = true)
        ManagePhase.Status -> ManageStatusList(client)
        ManagePhase.Choosing -> ManageNetworkList(client)
        is ManagePhase.Working -> StepView(
            icon = R.drawable.ic_wifi,
            busy = true,
            title = phase.message,
            message = "Het scherm wisselt van netwerk. Dit duurt meestal een halve minuut. Laat deze app open.",
            modifier = Modifier.testTag("step.working"),
        )
        is ManagePhase.Joined -> StepView(
            icon = R.drawable.ic_check,
            title = "Verbonden met ${phase.ssid}",
            message = if (phase.online) {
                "Het scherm is online."
            } else {
                phase.message ?: "Het scherm is verbonden, maar de server is nog niet bereikbaar."
            },
            modifier = Modifier.testTag("step.joined"),
        ) {
            PrimaryButton("Klaar", onClick = client::backToStatus, modifier = Modifier.testTag("button.done"))
        }
        is ManagePhase.JoinFailed -> StepView(
            icon = R.drawable.ic_signal_wifi_bad,
            tint = Palette.error,
            title = "Dat lukte niet",
            message = phase.message,
            modifier = Modifier.testTag("step.failed"),
        ) {
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                PrimaryButton("Opnieuw proberen", onClick = client::backToList, modifier = Modifier.testTag("button.retry"))
                SecondaryButton(
                    phase.ssid?.let { "Naar status ($it)" } ?: "Naar status",
                    onClick = client::backToStatus,
                    modifier = Modifier.testTag("button.status"),
                )
            }
        }
        ManagePhase.Expired -> StepView(
            icon = R.drawable.ic_qr_code_scanner,
            tint = Palette.error,
            title = "QR verlopen",
            message = ManageClient.EXPIRED_MESSAGE,
            modifier = Modifier.testTag("step.expired"),
        ) {
            PrimaryButton("Andere QR-code scannen", onClick = rescanQr)
        }
        ManagePhase.Outdated -> StepView(
            icon = R.drawable.ic_warning,
            tint = Palette.error,
            title = "Werk de app bij",
            message = "Dit scherm gebruikt een nieuwere versie van het beheer. Werk de app bij in de Play Store en scan de sticker opnieuw.",
            modifier = Modifier.testTag("step.outdated"),
        )
        ManagePhase.Unsupported -> StepView(
            icon = R.drawable.ic_warning,
            tint = Palette.error,
            title = "Beheer nog niet beschikbaar",
            message = "De speler van dit scherm is nog niet bijgewerkt voor beheer via de sticker. Probeer het later opnieuw.",
            modifier = Modifier.testTag("step.unsupported"),
        )
        is ManagePhase.Failed -> StepView(
            icon = R.drawable.ic_warning,
            tint = Palette.error,
            title = "Dat lukte niet",
            message = phase.message,
            modifier = Modifier.testTag("step.error"),
        ) {
            PrimaryButton("Opnieuw proberen", onClick = client::retry, modifier = Modifier.testTag("button.restart"))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ManageStatusList(client: ManageClient) {
    val status by client.status.collectAsStateWithLifecycle()
    val refreshing by client.isRefreshing.collectAsStateWithLifecycle()
    val reconnecting by client.isReconnecting.collectAsStateWithLifecycle()
    val connected by client.isConnected.collectAsStateWithLifecycle()

    Column(Modifier.fillMaxSize()) {
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = client::refresh,
            modifier = Modifier.weight(1f).fillMaxWidth().testTag("step.status"),
        ) {
            LazyColumn(
                contentPadding = PaddingValues(horizontal = 20.dp, vertical = 16.dp),
                verticalArrangement = Arrangement.spacedBy(22.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.fillMaxSize(),
            ) {
                if (reconnecting) {
                    item {
                        Row(
                            Modifier.widthIn(max = 560.dp).fillMaxWidth().card().padding(16.dp)
                                .semantics(mergeDescendants = true) {}
                                .testTag("label.reconnecting"),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Spinner()
                            Text("Verbinding met het scherm herstellen…", color = Palette.muted, fontSize = 15.sp)
                        }
                    }
                }
                val s = status ?: return@LazyColumn
                item { OnlineHeader(s, client.link.localName) }
                section("Netwerk") {
                    InfoRow("Wifi", wifiText(s), "label.network", mono = true) {
                        val signal = s.signal
                        if (signal != null && s.ssid != null) {
                            Icon(painterResource(signalIcon(signal)), null, tint = Palette.soft, modifier = Modifier.size(20.dp))
                        }
                    }
                    if (s.network == "ethernet") InfoRow("Kabel", "Verbonden", "label.ethernet")
                    InfoRow("IP-adres", s.ip ?: "Geen", "label.ip", mono = true, last = true)
                }
                section("Scherm") {
                    InfoRow("Toont", screenText(s.screenState), "label.screen")
                    InfoRow("Gekoppeld", s.paired?.let { if (it) "Ja" else "Nee" } ?: "Onbekend", "label.paired")
                    InfoRow("Spelerversie", s.agentVersion ?: "Onbekend", "label.version", mono = true, last = s.device == null)
                    s.device?.let { InfoRow("Apparaat", it, "label.device", last = true) }
                }
                val job = s.job
                val jobMsg = job?.msg
                if (job != null && job.op == "connect" && jobMsg != null) {
                    section("Laatste wifi-wijziging") {
                        Text(
                            jobMsg,
                            color = if (job.state == "failed") Palette.error else Palette.muted,
                            fontSize = 15.sp,
                            modifier = Modifier.fillMaxWidth().padding(18.dp).testTag("label.job"),
                        )
                    }
                }
            }
        }
        if (status?.canChangeWifi == true) {
            PrimaryButton(
                "Wifi wijzigen",
                onClick = client::chooseNetwork,
                enabled = connected,
                icon = R.drawable.ic_wifi,
                modifier = Modifier.widthIn(max = 560.dp).align(Alignment.CenterHorizontally)
                    .padding(horizontal = 20.dp, vertical = 12.dp).testTag("button.changeWifi"),
            )
        }
    }
}

private fun LazyListScope.section(title: String, rows: @Composable () -> Unit) {
    item {
        Column(Modifier.widthIn(max = 560.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Kicker(title, Modifier.padding(horizontal = 4.dp))
            Column(Modifier.fillMaxWidth().card()) { rows() }
        }
    }
}

@Composable
private fun InfoRow(
    label: String,
    value: String,
    tag: String,
    mono: Boolean = false,
    last: Boolean = false,
    accessory: @Composable () -> Unit = {},
) {
    Column {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 54.dp).semantics(mergeDescendants = true) {}.padding(horizontal = 18.dp)
                .testTag(tag),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(label, color = Palette.muted, fontSize = 16.sp)
            Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
                SelectionContainer {
                    Text(
                        value,
                        color = Palette.text,
                        fontSize = if (mono) 15.sp else 16.sp,
                        fontFamily = if (mono) Fonts.mono else Fonts.walsheim,
                        fontWeight = if (mono) FontWeight.Medium else FontWeight.Normal,
                        maxLines = 1,
                        overflow = TextOverflow.MiddleEllipsis,
                    )
                }
            }
            accessory()
        }
        if (!last) Box(Modifier.fillMaxWidth().height(1.dp).background(Palette.line))
    }
}

/** The screen's name in Impact with its online state as a chip (canvas "App scherm beheren"). */
@Composable
private fun OnlineHeader(status: ManageStatus, localName: String) {
    val online = status.online
    Column(
        Modifier.widthIn(max = 560.dp).fillMaxWidth().padding(horizontal = 4.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        ImpactTitle(status.name ?: localName, 56.sp, Modifier.testTag("label.screenName"))
        Chip(
            when (online) {
                true -> "Online"
                false -> "Offline: de server is niet bereikbaar"
                null -> "Online-status onbekend"
            },
            tone = when (online) {
                true -> Palette.ok
                false -> Palette.error
                null -> Palette.muted
            },
            modifier = Modifier.testTag("label.online"),
        )
    }
}

private fun wifiText(status: ManageStatus): String {
    val ssid = status.ssid ?: return "Geen"
    return status.signal?.let { "$ssid · $it%" } ?: ssid
}

private fun screenText(state: String?): String = when (state) {
    "PLAYING" -> "Content"
    "WAITING" -> "Wacht op content"
    "PAIRING" -> "Koppelcode"
    "WIFI_SETUP" -> "Wifi instellen"
    null -> "Onbekend"
    else -> state
}

@Composable
private fun ManageNetworkList(client: ManageClient) {
    val networks by client.networks.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val notice by client.notice.collectAsStateWithLifecycle()
    val status by client.status.collectAsStateWithLifecycle()
    var choice by remember { mutableStateOf<Choice?>(null) }

    NetworkPicker(
        title = "Wifi wijzigen",
        subtitle = "Kies het nieuwe wifi-netwerk voor het scherm",
        notice = notice,
        networks = networks,
        scanning = scanning,
        rescan = client::rescan,
        choose = { choice = Choice(it) },
        current = status?.ssid,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 0.dp).card().padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(painterResource(R.drawable.ic_info), null, tint = Palette.muted, modifier = Modifier.size(20.dp))
            Text(
                "Lukt het niet, dan gaat het scherm terug naar het netwerk dat het nu heeft.",
                color = Palette.muted,
                fontSize = 14.sp,
                lineHeight = 20.sp,
            )
        }
    }

    choice?.let { current ->
        PasswordSheet(
            network = current.network,
            onDismiss = { choice = null },
            connect = { ssid, password ->
                choice = null
                client.connect(ssid, password, hidden = current.network == null)
            },
        )
    }
}
