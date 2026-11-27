package nl.zekerzichtbaar.narrowcasting.ui

import androidx.annotation.DrawableRes
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.text
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import nl.zekerzichtbaar.narrowcasting.R

/** House style D3, the same tokens as the CMS beheer, the TV screens and the iOS app (Theme.swift). */
object Palette {
    val background = Color(0xFF0C0C11)
    val surface = Color(0xFF16161D)
    val ghost = Color(0xFF1E1E26)
    val line = Color(0xFF22222B)
    val line2 = Color(0xFF2A2A34)
    val text = Color.White
    val soft = Color(0xFFD4D4DC)
    val muted = Color(0xFFA9A9B6)
    val faint = Color(0xFF8A8A98)
    val dim = Color(0xFF6E6E7C)

    /** Brand and live only, never for errors. */
    val accent = Color(0xFFFF0080)
    val ok = Color(0xFF4ADE9B)
    val busy = Color(0xFFA3AEFF)
    val warn = Color(0xFFFFC24D)
    val error = Color(0xFFFF7A66)
}

/** GT Walsheim Pro for the UI, ZZ Impact for the big uppercase headings, Geist Mono for codes and kickers. */
object Fonts {
    val walsheim = FontFamily(
        Font(R.font.gt_walsheim_regular, FontWeight.Normal),
        Font(R.font.gt_walsheim_medium, FontWeight.Medium),
        Font(R.font.gt_walsheim_bold, FontWeight.Bold),
    )
    val impact = FontFamily(Font(R.font.zz_impact))
    val mono = FontFamily(Font(R.font.geist_mono_medium, FontWeight.Medium))
}

private fun walsheimTypography(): Typography {
    val base = Typography()
    fun TextStyle.w() = copy(fontFamily = Fonts.walsheim)
    return Typography(
        displayLarge = base.displayLarge.w(),
        displayMedium = base.displayMedium.w(),
        displaySmall = base.displaySmall.w(),
        headlineLarge = base.headlineLarge.w(),
        headlineMedium = base.headlineMedium.w(),
        headlineSmall = base.headlineSmall.w(),
        titleLarge = base.titleLarge.w(),
        titleMedium = base.titleMedium.w(),
        titleSmall = base.titleSmall.w(),
        bodyLarge = base.bodyLarge.w(),
        bodyMedium = base.bodyMedium.w(),
        bodySmall = base.bodySmall.w(),
        labelLarge = base.labelLarge.w(),
        labelMedium = base.labelMedium.w(),
        labelSmall = base.labelSmall.w(),
    )
}

@Composable
fun NarrowcastingTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Palette.accent,
            onPrimary = Color.White,
            background = Palette.background,
            onBackground = Palette.text,
            surface = Palette.background,
            onSurface = Palette.text,
            onSurfaceVariant = Palette.muted,
            surfaceContainerLow = Palette.surface,
            surfaceContainer = Palette.surface,
            surfaceContainerHigh = Palette.surface,
            surfaceContainerHighest = Palette.surface,
            outline = Palette.line2,
            outlineVariant = Palette.line,
            error = Palette.error,
        ),
        typography = walsheimTypography(),
        content = content,
    )
}

/** White pill on ink, the main action (canvas `btn-w`). */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    @DrawableRes icon: Int? = null,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.fillMaxWidth().heightIn(min = 56.dp),
        shape = CircleShape,
        contentPadding = PaddingValues(horizontal = 20.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.White,
            contentColor = Palette.background,
            disabledContainerColor = Color.White.copy(alpha = 0.35f),
            disabledContentColor = Palette.background,
        ),
    ) {
        ButtonLabel(text, icon, 17.sp)
    }
}

/** Outlined pill on ink (canvas `btn-g`); `compact` for the small header buttons. */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    compact: Boolean = false,
    @DrawableRes icon: Int? = null,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = if (compact) modifier.heightIn(min = 40.dp) else modifier.fillMaxWidth().heightIn(min = 56.dp),
        shape = CircleShape,
        border = BorderStroke(1.dp, Palette.line2),
        contentPadding = PaddingValues(horizontal = if (compact) 16.dp else 20.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = Palette.ghost,
            contentColor = Palette.text,
            disabledContainerColor = Palette.ghost,
            disabledContentColor = Palette.text.copy(alpha = 0.4f),
        ),
    ) {
        ButtonLabel(text, icon, if (compact) 15.sp else 17.sp)
    }
}

@Composable
private fun RowScope.ButtonLabel(text: String, @DrawableRes icon: Int?, size: TextUnit) {
    if (icon != null) {
        Icon(painterResource(icon), null, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
    }
    Text(text, fontFamily = Fonts.walsheim, fontSize = size, fontWeight = FontWeight.Medium)
}

private val cardShape = RoundedCornerShape(20.dp)

/** Surface card with a hairline (canvas `card`). */
fun Modifier.card(shape: Shape = cardShape): Modifier =
    background(Palette.surface, shape).border(1.dp, Palette.line, shape)

/** Big uppercase heading in ZZ Impact, line-height .95 as the canvas. */
@Composable
fun ImpactTitle(text: String, size: TextUnit, modifier: Modifier = Modifier, textAlign: TextAlign = TextAlign.Start) {
    Text(
        text.uppercase(),
        color = Palette.text,
        style = TextStyle(
            fontFamily = Fonts.impact,
            fontSize = size,
            lineHeight = size * 0.95f,
            lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None),
            textAlign = textAlign,
        ),
        modifier = modifier.semantics { heading() },
    )
}

/** Body text of a step, muted. */
@Composable
fun Lead(text: String, modifier: Modifier = Modifier, textAlign: TextAlign = TextAlign.Start) {
    Text(
        text,
        color = Palette.muted,
        fontFamily = Fonts.walsheim,
        fontSize = 17.sp,
        lineHeight = 24.sp,
        textAlign = textAlign,
        modifier = modifier,
    )
}

/** Mono uppercase label (canvas `kicker`). */
@Composable
fun Kicker(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        color = Palette.muted,
        fontFamily = Fonts.mono,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        letterSpacing = 0.12.em,
        modifier = modifier,
    )
}

/** Status pill with a dot (canvas `chip`); tone colours as the CMS. */
@Composable
fun Chip(text: String, tone: Color, modifier: Modifier = Modifier) {
    Row(
        modifier
            .background(if (tone == Palette.muted) Palette.ghost else tone.copy(alpha = 0.15f), CircleShape)
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(Modifier.size(7.dp).background(tone, CircleShape))
        Text(text, color = tone, fontFamily = Fonts.walsheim, fontWeight = FontWeight.Medium, fontSize = 13.sp)
    }
}

/** Ring with a magenta arc (canvas `spin`). */
@Composable
fun Spinner(modifier: Modifier = Modifier, size: Dp = 22.dp, stroke: Dp = 3.dp) {
    val turn by rememberInfiniteTransition(label = "spin").animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart),
        label = "turn",
    )
    Canvas(modifier.size(size).semantics { contentDescription = "Bezig" }) {
        val width = stroke.toPx()
        val inset = width / 2
        val arc = Size(this.size.width - width, this.size.height - width)
        val topLeft = Offset(inset, inset)
        drawArc(Palette.line2, 0f, 360f, false, topLeft, arc, style = Stroke(width))
        rotate(turn) {
            drawArc(Palette.accent, -90f, 90f, false, topLeft, arc, style = Stroke(width, cap = StrokeCap.Round))
        }
    }
}

/** The D3 mark: magenta z-blob, white z and (optionally) the TV sticker, white with ink on dark. */
@Composable
fun LogoMark(modifier: Modifier = Modifier, sticker: Boolean = true) {
    Image(
        painterResource(if (sticker) R.drawable.zz_logo else R.drawable.zz_mark),
        contentDescription = null,
        modifier = modifier,
    )
}

/** Wordmark: ZEKER bold, ZICHTBAAR regular, 0.14em tracking, stacked as on the app board. */
@Composable
fun Wordmark(modifier: Modifier = Modifier, size: TextUnit = 13.sp) {
    Column(
        modifier.clearAndSetSemantics { contentDescription = "Zeker Zichtbaar" },
        verticalArrangement = Arrangement.spacedBy(0.dp),
    ) {
        // lineHeight 1.1 like the CMS sidebar: the font's default leading puts the two words too far apart.
        Text("ZEKER", color = Palette.text, fontFamily = Fonts.walsheim, fontWeight = FontWeight.Bold,
            fontSize = size, letterSpacing = 0.14.em, lineHeight = size * 1.1)
        Text("ZICHTBAAR", color = Palette.text, fontFamily = Fonts.walsheim, fontSize = size, letterSpacing = 0.14.em,
            lineHeight = size * 1.1)
    }
}

/** Header row of every step: a small pill on the left, something on the right. */
@Composable
fun TopBar(
    modifier: Modifier = Modifier,
    leading: @Composable RowScope.() -> Unit = {},
    trailing: @Composable RowScope.() -> Unit = {},
) {
    Row(
        modifier.fillMaxWidth().heightIn(min = 40.dp).padding(horizontal = 20.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        leading()
        Spacer(Modifier.weight(1f).widthIn(min = 12.dp))
        trailing()
    }
}

/** Mono, uppercase id of the screen ("SCHERM 1E2F"). */
@Composable
fun DeviceLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        color = Palette.muted,
        fontFamily = Fonts.mono,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        letterSpacing = 0.08.em,
        maxLines = 1,
        modifier = modifier,
    )
}

/** "Vernieuwen" pill in the header, a spinner while scanning. */
@Composable
fun RescanButton(scanning: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    if (scanning) {
        Spinner(Modifier.padding(horizontal = 9.dp))
    } else {
        SecondaryButton("Vernieuwen", onClick = onClick, enabled = enabled, compact = true, icon = R.drawable.ic_refresh,
            modifier = modifier)
    }
}

/** Ink text field with a magenta ring while focused (canvas `field`). */
@Composable
fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedContainerColor = Palette.background,
    unfocusedContainerColor = Palette.background,
    errorContainerColor = Palette.background,
    focusedBorderColor = Palette.accent,
    unfocusedBorderColor = Palette.line2,
    errorBorderColor = Palette.error,
    focusedLabelColor = Palette.muted,
    unfocusedLabelColor = Palette.muted,
    cursorColor = Palette.accent,
    focusedTextColor = Palette.text,
    unfocusedTextColor = Palette.text,
    focusedPlaceholderColor = Palette.dim,
    unfocusedPlaceholderColor = Palette.dim,
    unfocusedSupportingTextColor = Palette.faint,
    focusedSupportingTextColor = Palette.faint,
    errorSupportingTextColor = Palette.error,
    focusedTrailingIconColor = Palette.muted,
    unfocusedTrailingIconColor = Palette.muted,
)

val fieldShape = RoundedCornerShape(14.dp)

/** An icon, an Impact heading and muted text, left-aligned as on the app boards; the base of every non-list step. */
@Composable
fun StepView(
    @DrawableRes icon: Int,
    title: String,
    message: String?,
    modifier: Modifier = Modifier,
    tint: Color = Palette.ok,
    busy: Boolean = false,
    accessory: @Composable ColumnScope.() -> Unit = {},
) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        val height = maxHeight
        Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            Column(
                Modifier.heightIn(min = height).widthIn(max = 568.dp).fillMaxWidth()
                    .padding(horizontal = 24.dp, vertical = 32.dp),
                verticalArrangement = Arrangement.spacedBy(28.dp, Alignment.CenterVertically),
            ) {
                StepIcon(icon, tint, busy)
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    ImpactTitle(title, 52.sp)
                    if (message != null) Lead(message)
                }
                accessory()
            }
        }
    }
}

@Composable
private fun StepIcon(@DrawableRes icon: Int, tint: Color, busy: Boolean) {
    val hidden = Modifier.clearAndSetSemantics {}
    when {
        busy -> Spinner(hidden, size = 64.dp, stroke = 6.dp)
        tint == Palette.ok -> Box(hidden.size(64.dp).background(Palette.ok, CircleShape), contentAlignment = Alignment.Center) {
            Icon(painterResource(R.drawable.ic_check), null, tint = Palette.background, modifier = Modifier.size(36.dp))
        }
        else -> Box(
            hidden.size(72.dp).background(tint.copy(alpha = 0.14f), RoundedCornerShape(22.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(painterResource(icon), null, tint = tint, modifier = Modifier.size(36.dp))
        }
    }
}

/** Concentric magenta rings around the z while looking for the screen (canvas "App scherm zoeken"). */
@Composable
fun SearchingView(slow: Boolean, title: String, message: String, modifier: Modifier = Modifier) {
    val pulse by rememberInfiniteTransition(label = "pulse").animateFloat(
        initialValue = 0.92f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Reverse),
        label = "scale",
    )
    BoxWithConstraints(modifier.fillMaxSize()) {
        val height = maxHeight
        Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            Column(
                Modifier.heightIn(min = height).fillMaxWidth().padding(horizontal = 24.dp, vertical = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(40.dp, Alignment.CenterVertically),
            ) {
                Box(Modifier.size(240.dp).clearAndSetSemantics {}, contentAlignment = Alignment.Center) {
                    val scale = if (slow) 1f else pulse
                    Box(
                        Modifier.size(240.dp * scale).border(2.dp, Palette.accent.copy(alpha = 0.18f), CircleShape),
                    )
                    Box(Modifier.size(168.dp).border(2.dp, Palette.accent.copy(alpha = 0.35f), CircleShape))
                    Box(Modifier.size(96.dp).background(Palette.accent.copy(alpha = 0.12f), CircleShape))
                    LogoMark(Modifier.size(width = 76.dp, height = 64.dp), sticker = false)
                }
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    ImpactTitle(title, 48.sp, textAlign = TextAlign.Center)
                    Lead(message, textAlign = TextAlign.Center)
                }
            }
        }
    }
}

/** The pairing code in mono boxes, the dash between them (canvas `code`). */
@Composable
fun PairingCodeBoxes(code: String, modifier: Modifier = Modifier) {
    SelectionContainer(modifier) {
        // One text for accessibility and tests ("K7Q-4M2"), not seven boxes.
        Row(Modifier.clearAndSetSemantics { text = AnnotatedString(code) }, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            code.forEach { char ->
                if (char == '-') {
                    Text("-", color = Palette.dim, fontFamily = Fonts.mono, fontSize = 24.sp,
                        textAlign = TextAlign.Center, modifier = Modifier.width(12.dp))
                } else {
                    Box(
                        Modifier.weight(1f, fill = false).widthIn(max = 44.dp).heightIn(min = 58.dp)
                            .background(Palette.surface, fieldShape).border(1.dp, Palette.line2, fieldShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            char.toString(),
                            color = Palette.text,
                            fontFamily = Fonts.mono,
                            fontWeight = FontWeight.Medium,
                            fontSize = 30.sp,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                        )
                    }
                }
            }
        }
    }
}
