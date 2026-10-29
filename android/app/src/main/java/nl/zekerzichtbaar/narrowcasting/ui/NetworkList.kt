package nl.zekerzichtbaar.narrowcasting.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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

private class Choice(val network: WifiNetwork?)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NetworkList(client: SetupClient, modifier: Modifier = Modifier) {
    val networks by client.networks.collectAsStateWithLifecycle()
    val scanning by client.isScanning.collectAsStateWithLifecycle()
    val notice by client.notice.collectAsStateWithLifecycle()
    var choice by remember { mutableStateOf<Choice?>(null) }

    PullToRefreshBox(isRefreshing = scanning, onRefresh = client::rescan, modifier = modifier.fillMaxSize().testTag("step.choosing")) {
        LazyColumn(
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            item {
                Text(
                    "Kies het wifi-netwerk voor het scherm",
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
                        NetworkRow(network) { choice = Choice(network) }
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
                            .clickable(role = Role.Button) { choice = Choice(null) }
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
                    "Trek de lijst omlaag om opnieuw te zoeken.",
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

@Composable
internal fun NetworkRow(network: WifiNetwork, current: Boolean = false, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .heightIn(min = 52.dp)
            .clickable(role = Role.Button, onClick = onClick)
            .semantics { stateDescription = "${if (network.secured) "beveiligd" else "open"}, signaal ${network.signal} procent" }
            .padding(horizontal = 16.dp)
            .testTag("network.${network.ssid}"),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            network.ssid,
            color = Palette.text,
            fontSize = 17.sp,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        if (network.secured) {
            Icon(painterResource(R.drawable.ic_lock), null, tint = Palette.muted, modifier = Modifier.size(16.dp))
        }
        Icon(painterResource(signalIcon(network.signal)), null, tint = Palette.soft, modifier = Modifier.size(22.dp))
        if (current) {
            Icon(painterResource(R.drawable.ic_check), "huidig netwerk", tint = Palette.accent, modifier = Modifier.size(20.dp))
        }
    }
}

internal fun signalIcon(signal: Int) = when {
    signal >= 75 -> R.drawable.ic_signal_wifi_4_bar
    signal >= 50 -> R.drawable.ic_network_wifi_3_bar
    signal >= 25 -> R.drawable.ic_network_wifi_2_bar
    else -> R.drawable.ic_network_wifi_1_bar
}

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
        containerColor = Palette.sheet,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 20.dp).navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    network?.ssid ?: "Ander netwerk",
                    color = Palette.text,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onDismiss) { Text("Annuleer", color = Palette.accent, fontSize = 16.sp) }
            }
            if (network == null) {
                OutlinedTextField(
                    value = ssid,
                    onValueChange = { ssid = it },
                    label = { Text("Netwerknaam") },
                    singleLine = true,
                    supportingText = { Text("Voor een verborgen netwerk: typ de naam precies zoals hij is ingesteld.") },
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.None,
                        autoCorrectEnabled = false,
                        imeAction = ImeAction.Next,
                    ),
                    keyboardActions = KeyboardActions(onNext = { passwordFocus.requestFocus() }),
                    colors = fieldColors(),
                    modifier = Modifier.fillMaxWidth().focusRequester(ssidFocus).testTag("field.ssid"),
                )
            }
            if (askPassword) {
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("Wachtwoord") },
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
                    colors = fieldColors(),
                    modifier = Modifier.fillMaxWidth().focusRequester(passwordFocus).testTag("field.password"),
                )
            } else {
                Text("Dit is een open netwerk zonder wachtwoord.", color = Palette.muted, fontSize = 16.sp)
            }
            if (tried && problem != null && !askPassword) {
                Text(problem, color = Palette.error, modifier = Modifier.testTag("label.problem"))
            }
            PrimaryButton("Verbinden", onClick = ::submit, modifier = Modifier.testTag("button.connect"))
        }
    }
}

@Composable
private fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = Palette.accent,
    focusedLabelColor = Palette.accent,
    cursorColor = Palette.accent,
    unfocusedBorderColor = Palette.line,
    focusedTextColor = Palette.text,
    unfocusedTextColor = Palette.text,
    unfocusedSupportingTextColor = Palette.muted,
    focusedSupportingTextColor = Palette.muted,
)
