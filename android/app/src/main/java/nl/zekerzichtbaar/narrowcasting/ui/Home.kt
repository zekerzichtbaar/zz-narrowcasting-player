package nl.zekerzichtbaar.narrowcasting.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import nl.zekerzichtbaar.narrowcasting.R
import nl.zekerzichtbaar.narrowcasting.setup.SetupLink

private const val NOT_A_SCREEN = "Deze QR-code hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont."

/** What the app shows before it has a link. */
@Composable
fun HomeScreen(message: String, open: (SetupLink) -> Unit) {
    val context = LocalContext.current
    var problem by rememberSaveable { mutableStateOf<String?>(null) }
    var typing by rememberSaveable { mutableStateOf(false) }

    StepView(
        icon = R.drawable.ic_qr_code_scanner,
        title = "Scan de QR-code op het scherm",
        message = problem ?: message,
        modifier = Modifier.safeDrawingPadding().testTag("step.home"),
    ) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            PrimaryButton(
                "Scan de QR-code",
                onClick = {
                    scanQr(
                        context,
                        found = { text ->
                            val link = SetupLink.parse(text)
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
            TextButton(onClick = { typing = true }, modifier = Modifier.fillMaxWidth().testTag("button.typeLink")) {
                Text("Link plakken of typen", color = Palette.accent, fontSize = 16.sp)
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
private fun LinkDialog(dismiss: () -> Unit, open: (SetupLink) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    var wrong by rememberSaveable { mutableStateOf(false) }

    fun submit() {
        val link = SetupLink.parse(text)
        if (link != null) open(link) else wrong = true
    }

    AlertDialog(
        onDismissRequest = dismiss,
        containerColor = Palette.sheet,
        title = { Text("Link van het scherm", color = Palette.text) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Plak de link uit de QR-code op het scherm.", color = Palette.muted)
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
                    modifier = Modifier.fillMaxWidth().testTag("field.link"),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = ::submit, modifier = Modifier.testTag("button.openLink")) {
                Text("Openen", color = Palette.accent)
            }
        },
        dismissButton = {
            TextButton(onClick = dismiss) { Text("Annuleer", color = Palette.accent) }
        },
    )
}
