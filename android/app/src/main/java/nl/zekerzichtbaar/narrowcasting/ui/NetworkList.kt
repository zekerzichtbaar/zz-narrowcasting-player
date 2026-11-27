package nl.zekerzichtbaar.narrowcasting.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import nl.zekerzichtbaar.narrowcasting.R
import nl.zekerzichtbaar.narrowcasting.setup.SetupClient
import nl.zekerzichtbaar.narrowcasting.setup.Validation
import nl.zekerzichtbaar.narrowcasting.setup.WifiNetwork

internal class Choice(val network: WifiNetwork?)

@Composable
fun NetworkList(client: SetupClient, modifier: Modifier = Modifier) {
    val networks by client.networks.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val notice by client.notice.collectAsStateWithLifecycle()
    var choice by remember { mutableStateOf<Choice?>(null) }

    NetworkPicker(
        title = "Wifi instellen",
        subtitle = "Kies het wifi-netwerk voor het scherm",
        notice = notice,
        networks = networks,
        scanning = scanning,
        rescan = client::rescan,
        choose = { choice = Choice(it) },
        modifier = modifier,
    ) {
        Text("Trek de lijst omlaag om opnieuw te zoeken.", color = Palette.faint, fontSize = 14.sp)
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

/** Impact heading, the networks in one card and a footer (canvas "App netwerk kiezen"); shared by setup and management. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun NetworkPicker(
    title: String,
    subtitle: String,
    notice: String?,
    networks: List<WifiNetwork>,
    scanning: Boolean,
    rescan: () -> Unit,
    choose: (WifiNetwork?) -> Unit,
    modifier: Modifier = Modifier,
    current: String? = null,
    footer: @Composable () -> Unit,
) {
    PullToRefreshBox(isRefreshing = scanning, onRefresh = rescan, modifier = modifier.fillMaxSize().testTag("step.choosing")) {
        LazyColumn(
            contentPadding = PaddingValues(horizontal = 20.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(22.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.fillMaxSize(),
        ) {
            item {
                Column(
                    Modifier.widthIn(max = 560.dp).fillMaxWidth().padding(horizontal = 4.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    ImpactTitle(title, 48.sp)
                    Text(subtitle, color = Palette.muted, fontSize = 16.sp)
                }
            }
            notice?.let { text ->
                item { Notice(text) }
            }
            item {
                Column(Modifier.widthIn(max = 560.dp).fillMaxWidth().card().clip(RoundedCornerShape(20.dp))) {
                    networks.forEach { network ->
                        NetworkRow(network, current = network.ssid == current) { choose(network) }
                        Separator()
                    }
                    if (networks.isEmpty()) {
                        Text(
                            if (scanning) "Netwerken zoeken…" else "Geen netwerken gevonden. Trek omlaag om opnieuw te zoeken.",
                            color = Palette.muted,
                            fontSize = 16.sp,
                            modifier = Modifier.heightIn(min = 60.dp).padding(horizontal = 18.dp, vertical = 18.dp),
                        )
                        Separator()
                    }
                    Row(
                        Modifier.fillMaxWidth()
                            .heightIn(min = 60.dp)
                            .clickable(role = Role.Button) { choose(null) }
                            .padding(horizontal = 18.dp)
                            .testTag("network.other"),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                    ) {
                        Icon(painterResource(R.drawable.ic_add), null, tint = Palette.muted, modifier = Modifier.size(24.dp))
                        Text("Ander netwerk…", color = Palette.muted, fontSize = 17.sp)
                    }
                }
            }
            item {
                Box(Modifier.widthIn(max = 560.dp).fillMaxWidth().padding(horizontal = 4.dp)) { footer() }
            }
        }
    }
}

@Composable
private fun Notice(text: String) {
    Row(
        Modifier.widthIn(max = 560.dp).fillMaxWidth()
            .background(Palette.error.copy(alpha = 0.15f), RoundedCornerShape(20.dp))
            .padding(16.dp)
            .testTag("label.notice"),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(painterResource(R.drawable.ic_warning), null, tint = Palette.error, modifier = Modifier.size(20.dp))
        Text(text, color = Palette.error, fontSize = 15.sp)
    }
}

@Composable
private fun Separator() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(Palette.line))
}

/** Strong signal white, medium soft, weak faint (canvas). */
private fun signalColor(signal: Int) = when {
    signal >= 67 -> Palette.text
    signal >= 34 -> Palette.soft
    else -> Palette.faint
}

@Composable
internal fun NetworkRow(network: WifiNetwork, current: Boolean = false, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .heightIn(min = 60.dp)
            .clickable(role = Role.Button, onClick = onClick)
            .semantics { stateDescription = "${if (network.secured) "beveiligd" else "open"}, signaal ${network.signal} procent" }
            .padding(horizontal = 18.dp)
            .testTag("network.${network.ssid}"),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Icon(painterResource(signalIcon(network.signal)), null, tint = signalColor(network.signal), modifier = Modifier.size(24.dp))
        Text(
            network.ssid,
            color = Palette.text,
            fontSize = 17.sp,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        if (current) {
            Icon(painterResource(R.drawable.ic_check), "huidig netwerk", tint = Palette.ok, modifier = Modifier.size(20.dp))
        }
        if (network.secured) {
            Icon(painterResource(R.drawable.ic_lock), null, tint = Palette.faint, modifier = Modifier.size(16.dp))
        }
        Icon(painterResource(R.drawable.ic_chevron_right), null, tint = Palette.dim, modifier = Modifier.size(20.dp))
    }
}

internal fun signalIcon(signal: Int) = when {
    signal >= 75 -> R.drawable.ic_signal_wifi_4_bar
    signal >= 50 -> R.drawable.ic_network_wifi_3_bar
    signal >= 25 -> R.drawable.ic_network_wifi_2_bar
    else -> R.drawable.ic_network_wifi_1_bar
}

/** Network name and password (canvas "App wachtwoord"). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun PasswordSheet(network: WifiNetwork?, onDismiss: () -> Unit, connect: (String, String) -> Unit) {
    var ssid by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var reveal by rememberSaveable { mutableStateOf(false) }
    var tried by rememberSaveable { mutableStateOf(false) }
    val ssidFocus = remember { FocusRequester() }
    val passwordFocus = remember { FocusRequester() }

    val secured = network?.secured ?: false
    val askPassword = secured || network == null
    val name = network?.ssid ?: ssid.trim(' ', '\t')
    val problem = (if (network == null) Validation.ssid(ssid) else null) ?: Validation.password(password, secured)

    fun submit() {
        tried = true
        if (problem == null) connect(name, password)
    }

    LaunchedEffect(Unit) {
        when {
            network == null -> ssidFocus.requestFocus()
            secured -> passwordFocus.requestFocus()
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Palette.background,
        contentColor = Palette.text,
        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
        dragHandle = null,
    ) {
        Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
            TopBar(
                Modifier.padding(top = 20.dp),
                leading = { SecondaryButton("Annuleer", onClick = onDismiss, compact = true) },
            )
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState())
                    .padding(horizontal = 20.dp, vertical = 24.dp),
                verticalArrangement = Arrangement.spacedBy(24.dp),
            ) {
                Column(Modifier.padding(horizontal = 4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Kicker("Netwerk")
                    ImpactTitle(network?.ssid ?: "Ander netwerk", 44.sp)
                }
                if (network == null) {
                    FieldBox("Netwerknaam") {
                        OutlinedTextField(
                            value = ssid,
                            onValueChange = { ssid = it },
                            placeholder = { Text("Netwerknaam") },
                            singleLine = true,
                            supportingText = { Text("Voor een verborgen netwerk: typ de naam precies zoals hij is ingesteld.") },
                            keyboardOptions = KeyboardOptions(
                                capitalization = KeyboardCapitalization.None,
                                autoCorrectEnabled = false,
                                imeAction = ImeAction.Next,
                            ),
                            keyboardActions = KeyboardActions(onNext = { passwordFocus.requestFocus() }),
                            textStyle = fieldText,
                            shape = fieldShape,
                            colors = fieldColors(),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).focusRequester(ssidFocus).testTag("field.ssid"),
                        )
                    }
                }
                if (askPassword) {
                    FieldBox("Wachtwoord") {
                        OutlinedTextField(
                            value = password,
                            onValueChange = { password = it },
                            placeholder = { Text("Wachtwoord") },
                            singleLine = true,
                            isError = tried && problem != null,
                            visualTransformation = if (reveal) VisualTransformation.None else PasswordVisualTransformation(),
                            trailingIcon = {
                                IconButton(onClick = { reveal = !reveal }) {
                                    Icon(
                                        painterResource(if (reveal) R.drawable.ic_visibility_off else R.drawable.ic_visibility),
                                        contentDescription = if (reveal) "Verberg wachtwoord" else "Toon wachtwoord",
                                        tint = Palette.muted,
                                    )
                                }
                            },
                            supportingText = when {
                                tried && problem != null -> {
                                    { Text(problem, color = Palette.error, modifier = Modifier.testTag("label.problem")) }
                                }
                                network == null -> {
                                    { Text("Laat leeg bij een open netwerk.") }
                                }
                                else -> null
                            },
                            keyboardOptions = KeyboardOptions(
                                capitalization = KeyboardCapitalization.None,
                                autoCorrectEnabled = false,
                                keyboardType = KeyboardType.Password,
                                imeAction = ImeAction.Go,
                            ),
                            keyboardActions = KeyboardActions(onGo = { submit() }),
                            textStyle = fieldText,
                            shape = fieldShape,
                            colors = fieldColors(),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).focusRequester(passwordFocus)
                                .testTag("field.password"),
                        )
                    }
                } else {
                    Text(
                        "Dit is een open netwerk zonder wachtwoord.",
                        color = Palette.muted,
                        fontSize = 16.sp,
                        modifier = Modifier.fillMaxWidth().card().padding(16.dp),
                    )
                }
                if (tried && problem != null && !askPassword) {
                    Text(problem, color = Palette.error, modifier = Modifier.testTag("label.problem"))
                }
            }
            PrimaryButton(
                "Verbinden",
                onClick = ::submit,
                modifier = Modifier.padding(start = 20.dp, end = 20.dp, bottom = 16.dp).testTag("button.connect"),
            )
        }
    }
}

private val fieldText = androidx.compose.ui.text.TextStyle(fontFamily = Fonts.walsheim, fontSize = 18.sp)

/** Label above an ink input (canvas `field`). */
@Composable
private fun FieldBox(label: String, field: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(label, color = Palette.text, fontSize = 14.sp, fontWeight = FontWeight.Medium)
        field()
    }
}
