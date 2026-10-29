package nl.zekerzichtbaar.narrowcasting.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
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
import androidx.compose.ui.semantics.Role
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
import nl.zekerzichtbaar.narrowcasting.setup.WifiNetwork

/** Status and wifi of a paired screen, opened from the QR sticker on the player. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ManageFlow(client: ManageClient, rescanQr: () -> Unit, close: () -> Unit) {
    val phase by client.phase.collectAsStateWithLifecycle()
    val status by client.status.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val refreshing by client.isRefreshing.collectAsStateWithLifecycle()
    val connected by client.isConnected.collectAsStateWithLifecycle()
    val fix = rememberBluetoothFixes(client, client.link.deviceId, client.usesBluetooth, client::recheckBluetooth)

    BackHandler(enabled = phase == ManagePhase.Choosing, onBack = client::backToStatus)

    Scaffold(
        containerColor = Palette.background,
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        status?.name ?: "Scherm ${client.link.deviceId}",
                        fontSize = 17.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    if (phase == ManagePhase.Choosing) {
                        TextButton(onClick = client::backToStatus, modifier = Modifier.testTag("button.backToStatus")) {
                            Icon(painterResource(R.drawable.ic_chevron_left), null, tint = Palette.accent, modifier = Modifier.size(20.dp))
                            Text("Status", color = Palette.accent, fontSize = 16.sp)
                        }
                    } else {
                        TextButton(onClick = close, modifier = Modifier.testTag("button.close")) {
                            Text("Klaar", color = Palette.accent, fontSize = 16.sp)
                        }
                    }
                },
                actions = {
                    when (phase) {
                        ManagePhase.Status -> if (refreshing) {
                            ToolbarSpinner()
                        } else {
                            IconButton(onClick = client::refresh, enabled = connected, modifier = Modifier.testTag("button.refresh")) {
                                Icon(
                                    painterResource(R.drawable.ic_refresh),
                                    "Vernieuwen",
                                    tint = if (connected) Palette.accent else Palette.muted,
                                )
                            }
                        }
                        ManagePhase.Choosing -> if (scanning) {
                            ToolbarSpinner()
                        } else {
                            IconButton(onClick = client::rescan, modifier = Modifier.testTag("button.rescan")) {
                                Icon(painterResource(R.drawable.ic_refresh), "Vernieuwen", tint = Palette.accent)
                            }
                        }
                        else -> Unit
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
            label = "managePhase",
        ) { current ->
            ManagePhaseContent(current, client, rescanQr, fix)
        }
    }
}

@Composable
private fun ToolbarSpinner() {
    CircularProgressIndicator(
        color = Palette.muted,
        strokeWidth = 2.dp,
        modifier = Modifier.padding(horizontal = 14.dp).size(20.dp),
    )
}

@Composable
private fun ManagePhaseContent(
    phase: ManagePhase,
    client: ManageClient,
    rescanQr: () -> Unit,
    fix: (BluetoothProblem) -> Unit,
) {
    when (phase) {
        is ManagePhase.Searching -> StepView(
            icon = R.drawable.ic_bluetooth_searching,
            busy = !phase.slow,
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
            icon = R.drawable.ic_check_circle,
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
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                PrimaryButton("Opnieuw proberen", onClick = client::backToList, modifier = Modifier.testTag("button.retry"))
                TextButton(onClick = client::backToStatus, modifier = Modifier.fillMaxWidth().testTag("button.status")) {
                    Text(phase.ssid?.let { "Naar status ($it)" } ?: "Naar status", color = Palette.accent, fontSize = 16.sp)
                }
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

    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = client::refresh,
        modifier = Modifier.fillMaxSize().testTag("step.status"),
    ) {
        LazyColumn(
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            if (reconnecting) {
                item {
                    Row(
                        Modifier.fillMaxWidth().background(Palette.card, RoundedCornerShape(12.dp)).padding(14.dp)
                            .testTag("label.reconnecting"),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        CircularProgressIndicator(color = Palette.muted, strokeWidth = 2.dp, modifier = Modifier.size(18.dp))
                        Text("Verbinding met het scherm herstellen…", color = Palette.muted, fontSize = 15.sp)
                    }
                }
            }
            val s = status ?: return@LazyColumn
            item { OnlineHeader(s, client.link.localName) }
            section("Netwerk") {
                InfoRow("Wifi", wifiText(s), "label.network") {
                    val signal = s.signal
                    if (signal != null && s.ssid != null) {
                        Icon(painterResource(signalIcon(signal)), null, tint = Palette.soft, modifier = Modifier.size(20.dp))
                    }
                }
                if (s.network == "ethernet") InfoRow("Kabel", "Verbonden", "label.ethernet")
                InfoRow("IP-adres", s.ip ?: "Geen", "label.ip")
            }
            section("Scherm") {
                InfoRow("Toont", screenText(s.screenState), "label.screen")
                InfoRow("Gekoppeld", s.paired?.let { if (it) "Ja" else "Nee" } ?: "Onbekend", "label.paired")
                InfoRow("Spelerversie", s.agentVersion ?: "Onbekend", "label.version")
                s.device?.let { InfoRow("Apparaat", it, "label.device") }
            }
            val job = s.job
            val jobMsg = job?.msg
            if (job != null && job.op == "connect" && jobMsg != null) {
                section("Laatste wifi-wijziging") {
                    Text(
                        jobMsg,
                        color = if (job.state == "failed") Palette.error else Palette.muted,
                        fontSize = 15.sp,
                        modifier = Modifier.padding(16.dp).testTag("label.job"),
                    )
                }
            }
            if (s.canChangeWifi) {
                item {
                    PrimaryButton(
                        "Wifi wijzigen",
                        onClick = client::chooseNetwork,
                        enabled = connected,
                        modifier = Modifier.padding(top = 12.dp).testTag("button.changeWifi"),
                    )
                }
            }
        }
    }
}

private fun LazyListScope.section(title: String, rows: @Composable () -> Unit) {
    item {
        Column(Modifier.fillMaxWidth().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                title.uppercase(),
                color = Palette.muted,
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 1.sp,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
            Column(Modifier.fillMaxWidth().background(Palette.card, RoundedCornerShape(12.dp))) { rows() }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String, tag: String, accessory: @Composable () -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics(mergeDescendants = true) {}.padding(horizontal = 16.dp)
            .testTag(tag),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(label, color = Palette.muted, fontSize = 16.sp)
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
            SelectionContainer {
                Text(value, color = Palette.text, fontSize = 16.sp, maxLines = 1, overflow = TextOverflow.MiddleEllipsis)
            }
        }
        accessory()
    }
}

@Composable
private fun OnlineHeader(status: ManageStatus, localName: String) {
    val online = status.online
    val tint = if (online == true) Palette.accent else Palette.error
    Row(
        Modifier.fillMaxWidth().background(Palette.card, RoundedCornerShape(12.dp)).padding(16.dp)
            .semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Box(Modifier.size(52.dp).background(tint.copy(alpha = 0.14f), CircleShape), contentAlignment = Alignment.Center) {
            Icon(
                painterResource(if (online == true) R.drawable.ic_check_circle else R.drawable.ic_signal_wifi_bad),
                null,
                tint = tint,
                modifier = Modifier.size(26.dp),
            )
        }
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                status.name ?: localName,
                color = Palette.text,
                fontSize = 17.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.testTag("label.screenName"),
            )
            Text(
                when (online) {
                    true -> "Online"
                    false -> "Offline: de server is niet bereikbaar"
                    null -> "Online-status onbekend"
                },
                color = if (online == true) Palette.accent else Palette.muted,
                fontSize = 15.sp,
                modifier = Modifier.testTag("label.online"),
            )
        }
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

private class ManageChoice(val network: WifiNetwork?)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ManageNetworkList(client: ManageClient) {
    val networks by client.networks.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val notice by client.notice.collectAsStateWithLifecycle()
    val status by client.status.collectAsStateWithLifecycle()
    var choice by remember { mutableStateOf<ManageChoice?>(null) }

    PullToRefreshBox(
        isRefreshing = scanning,
        onRefresh = client::rescan,
        modifier = Modifier.fillMaxSize().testTag("step.choosing"),
    ) {
        LazyColumn(
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            item {
                Text(
                    "Kies het nieuwe wifi-netwerk voor het scherm",
                    color = Palette.muted,
                    fontSize = 15.sp,
                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
                )
            }
            notice?.let { text ->
                item {
                    Row(
                        Modifier.fillMaxWidth()
                            .background(Palette.error.copy(alpha = 0.12f), RoundedCornerShape(12.dp))
                            .padding(14.dp)
                            .testTag("label.notice"),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Icon(painterResource(R.drawable.ic_warning), null, tint = Palette.error, modifier = Modifier.size(20.dp))
                        Text(text, color = Palette.error, fontSize = 15.sp)
                    }
                }
            }
            item {
                Column(Modifier.fillMaxWidth().background(Palette.card, RoundedCornerShape(12.dp))) {
                    networks.forEach { network ->
                        NetworkRow(network, current = network.ssid == status?.ssid) { choice = ManageChoice(network) }
                        HorizontalDivider(color = Palette.line.copy(alpha = 0.6f), modifier = Modifier.padding(start = 16.dp))
                    }
                    if (networks.isEmpty()) {
                        Text(
                            if (scanning) "Netwerken zoeken…" else "Geen netwerken gevonden. Trek omlaag om opnieuw te zoeken.",
                            color = Palette.muted,
                            fontSize = 16.sp,
                            modifier = Modifier.padding(16.dp),
                        )
                        HorizontalDivider(color = Palette.line.copy(alpha = 0.6f), modifier = Modifier.padding(start = 16.dp))
                    }
                    Row(
                        Modifier.fillMaxWidth()
                            .heightIn(min = 52.dp)
                            .clickable(role = Role.Button) { choice = ManageChoice(null) }
                            .padding(horizontal = 16.dp)
                            .testTag("network.other"),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(painterResource(R.drawable.ic_add), null, tint = Palette.accent, modifier = Modifier.size(20.dp))
                        Text("Ander netwerk…", color = Palette.accent, fontSize = 17.sp)
                    }
                }
            }
            item {
                Text(
                    "Lukt het niet, dan gaat het scherm terug naar het netwerk dat het nu heeft.",
                    color = Palette.muted,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
            }
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
