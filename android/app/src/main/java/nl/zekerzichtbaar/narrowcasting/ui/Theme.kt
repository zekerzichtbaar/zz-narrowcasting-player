package nl.zekerzichtbaar.narrowcasting.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The TV screens' palette (pi_player/screens.py, portal.py). */
object Palette {
    val background = Color(0xFF07080A)
    val text = Color(0xFFF2F4F6)
    val soft = Color(0xFFC9D1DB)
    val muted = Color(0xFFA3ABB7)
    val line = Color(0xFF3A414C)
    val accent = Color(0xFF3DDC84)
    val onAccent = Color(0xFF04200E)
    val error = Color(0xFFFF6E64)
    val card = Color.White.copy(alpha = 0.06f)
    val sheet = Color(0xFF15171B)
}

@Composable
fun NarrowcastingTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Palette.accent,
            onPrimary = Palette.onAccent,
            background = Palette.background,
            onBackground = Palette.text,
            surface = Palette.background,
            onSurface = Palette.text,
            onSurfaceVariant = Palette.muted,
            surfaceContainerLow = Palette.sheet,
            surfaceContainer = Palette.sheet,
            surfaceContainerHigh = Palette.sheet,
            surfaceContainerHighest = Palette.sheet,
            outline = Palette.line,
            outlineVariant = Palette.line,
            error = Palette.error,
        ),
        content = content,
    )
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().heightIn(min = 52.dp),
        shape = RoundedCornerShape(14.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Palette.accent,
            contentColor = Palette.onAccent,
            disabledContainerColor = Palette.accent.copy(alpha = 0.35f),
            disabledContentColor = Palette.onAccent,
        ),
    ) {
        Text(text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** A centred icon + title + text block, the base of every non-list step. */
@Composable
fun StepView(
    @DrawableRes icon: Int,
    title: String,
    message: String?,
    modifier: Modifier = Modifier,
    tint: Color = Palette.accent,
    busy: Boolean = false,
    accessory: @Composable ColumnScope.() -> Unit = {},
) {
    Box(modifier.fillMaxSize().verticalScroll(rememberScrollState()), contentAlignment = Alignment.Center) {
        Column(
            modifier = Modifier.widthIn(max = 520.dp).fillMaxWidth().padding(horizontal = 24.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Box(
                Modifier.size(96.dp).background(tint.copy(alpha = 0.14f), CircleShape).clearAndSetSemantics {},
                contentAlignment = Alignment.Center,
            ) {
                if (busy) {
                    CircularProgressIndicator(color = tint, strokeWidth = 3.dp, modifier = Modifier.size(40.dp))
                } else {
                    Icon(painterResource(icon), contentDescription = null, tint = tint, modifier = Modifier.size(44.dp))
                }
            }
            Text(
                title,
                color = Palette.text,
                fontSize = 22.sp,
                lineHeight = 28.sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            if (message != null) {
                Text(message, color = Palette.muted, fontSize = 17.sp, lineHeight = 23.sp, textAlign = TextAlign.Center)
            }
            accessory()
        }
    }
}
