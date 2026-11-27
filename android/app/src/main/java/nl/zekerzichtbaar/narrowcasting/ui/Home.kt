package nl.zekerzichtbaar.narrowcasting.ui

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import nl.zekerzichtbaar.narrowcasting.R
import nl.zekerzichtbaar.narrowcasting.setup.ScreenLink

private const val NOT_A_SCREEN =
    "Deze QR-code hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont, of de QR-sticker op de speler."

/** What the app shows before it has a link (canvas "App start"). */
@Composable
fun HomeScreen(message: String, open: (ScreenLink) -> Unit) {
    val context = LocalContext.current
    var problem by rememberSaveable { mutableStateOf<String?>(null) }
    var typing by rememberSaveable { mutableStateOf(false) }

    Box(Modifier.fillMaxSize().background(Palette.background), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.safeDrawingPadding().widthIn(max = 560.dp).fillMaxSize()
                .padding(start = 24.dp, end = 24.dp, top = 16.dp, bottom = 8.dp)
                .testTag("step.home"),
            verticalArrangement = Arrangement.spacedBy(28.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LogoMark(Modifier.size(width = 44.dp, height = 35.dp))
                Wordmark()
            }
            Column(
                Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(40.dp, Alignment.CenterVertically),
            ) {
                TvWithQr(Modifier.align(Alignment.CenterHorizontally))
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    ImpactTitle("Scan de QR-code op het scherm", 52.sp)
                    Lead(problem ?: message)
                }
            }
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                PrimaryButton(
                    "Scan de QR-code",
                    icon = R.drawable.ic_qr_code_scanner,
                    onClick = {
                        scanQr(
                            context,
                            found = { text ->
                                val link = ScreenLink.parse(text)
                                if (link != null) {
                                    problem = null
                                    open(link)
                                } else {
                                    problem = NOT_A_SCREEN
                                }
                            },
                            unavailable = {
                                problem = "De QR-scanner is niet beschikbaar op deze telefoon. Plak of typ de link van het scherm."
                                typing = true
                            },
                        )
                    },
                    modifier = Modifier.testTag("button.scan"),
                )
                SecondaryButton(
                    "Link plakken of typen",
                    onClick = { typing = true },
                    modifier = Modifier.testTag("button.typeLink"),
                )
            }
        }
    }

    if (typing) {
        LinkDialog(
            dismiss = { typing = false },
            open = { link ->
                typing = false
                problem = null
                open(link)
            },
        )
    }
}

/** A TV showing a QR code, with the live dot (canvas "App start"). */
@Composable
private fun TvWithQr(modifier: Modifier = Modifier) {
    Column(
        modifier.clearAndSetSemantics {},
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        val screen = RoundedCornerShape(18.dp)
        Box(
            Modifier.size(width = 250.dp, height = 164.dp).background(Palette.surface, screen).border(5.dp, Palette.line2, screen),
            contentAlignment = Alignment.Center,
        ) {
            Box(
                Modifier.size(96.dp).background(Color.White, RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(painterResource(R.drawable.ic_qr_code), null, tint = Palette.background, modifier = Modifier.size(60.dp))
            }
            Box(Modifier.align(Alignment.TopEnd).padding(14.dp).size(10.dp).background(Palette.accent, CircleShape))
        }
        Box(Modifier.size(width = 80.dp, height = 6.dp).background(Palette.line2, CircleShape))
    }
}

private fun scanQr(context: Context, found: (String) -> Unit, unavailable: () -> Unit) {
    val options = GmsBarcodeScannerOptions.Builder()
        .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
        .enableAutoZoom()
        .build()
    GmsBarcodeScanning.getClient(context, options).startScan()
        .addOnSuccessListener { barcode -> found(barcode.rawValue.orEmpty()) }
        .addOnFailureListener { unavailable() }
}

@Composable
private fun LinkDialog(dismiss: () -> Unit, open: (ScreenLink) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    var wrong by rememberSaveable { mutableStateOf(false) }

    fun submit() {
        val link = ScreenLink.parse(text)
        if (link != null) open(link) else wrong = true
    }

    AlertDialog(
        onDismissRequest = dismiss,
        containerColor = Palette.surface,
        shape = RoundedCornerShape(20.dp),
        title = { Text("Link van het scherm", color = Palette.text, fontFamily = Fonts.walsheim, fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Plak de link uit de QR-code op het scherm of de sticker.", color = Palette.muted)
                OutlinedTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        wrong = false
                    },
                    placeholder = { Text("https://app.zekerzichtbaar.tv/setup?…") },
                    singleLine = true,
                    isError = wrong,
                    supportingText = if (wrong) {
                        { Text("Dit is geen link van een scherm.", color = Palette.error) }
                    } else {
                        null
                    },
                    keyboardOptions = KeyboardOptions(
                        autoCorrectEnabled = false,
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Go,
                    ),
                    keyboardActions = KeyboardActions(onGo = { submit() }),
                    shape = fieldShape,
                    colors = fieldColors(),
                    modifier = Modifier.fillMaxWidth().testTag("field.link"),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = ::submit, modifier = Modifier.testTag("button.openLink")) {
                Text("Openen", color = Palette.text, fontWeight = FontWeight.Medium, fontSize = 16.sp)
            }
        },
        dismissButton = {
            TextButton(onClick = dismiss) { Text("Annuleer", color = Palette.muted, fontWeight = FontWeight.Medium, fontSize = 16.sp) }
        },
    )
}
