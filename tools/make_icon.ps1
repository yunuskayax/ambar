# Ambar simgesini üretir: icon.png (256 px) ve çok boyutlu icon.ico
# Kullanım: powershell -ExecutionPolicy Bypass -File tools\make_icon.ps1
param([string]$Out = (Join-Path $PSScriptRoot ".."))
Add-Type -AssemblyName System.Drawing

function RoundRect($x, $y, $w, $h, $r) {
    $p = New-Object Drawing.Drawing2D.GraphicsPath
    $p.AddArc($x, $y, $r, $r, 180, 90); $p.AddArc($x + $w - $r, $y, $r, $r, 270, 90)
    $p.AddArc($x + $w - $r, $y + $h - $r, $r, $r, 0, 90); $p.AddArc($x, $y + $h - $r, $r, $r, 90, 90)
    $p.CloseFigure(); return $p
}
function C($hex) { return [Drawing.ColorTranslator]::FromHtml($hex) }

$S = 256
$bmp = New-Object Drawing.Bitmap $S, $S
$g = [Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'; $g.PixelOffsetMode = 'HighQuality'
$g.Clear([Drawing.Color]::Transparent)

# Zemin: kehribar degrade
$zemin = New-Object Drawing.Drawing2D.LinearGradientBrush (New-Object Drawing.Point 0, 0), (New-Object Drawing.Point $S, $S), (C '#fbbf24'), (C '#c2410c')
$g.FillPath($zemin, (RoundRect 8 8 240 240 64))

$koyu = C '#1c1917'
$beyaz = [Drawing.Brushes]::White

# Ambar gövdesi: kırma (gambrel) çatılı klasik ambar silueti
function P($x, $y) { New-Object Drawing.Point $x, $y }
$govde = [Drawing.Point[]]@((P 50 132), (P 76 82), (P 128 58), (P 180 82), (P 206 132), (P 206 214), (P 50 214))
$g.FillPolygon($beyaz, $govde)

# Çatı: koyu kırma çatı çizgisi
$cati = New-Object Drawing.Pen $koyu, 16
$cati.StartCap = 'Round'; $cati.EndCap = 'Round'; $cati.LineJoin = 'Round'
$g.DrawLines($cati, [Drawing.Point[]]@((P 38 140), (P 70 80), (P 128 52), (P 186 80), (P 218 140)))

# Yan duvar tahtaları
$tahta = New-Object Drawing.Pen (C '#d6d3d1'), 4
foreach ($y in 156, 176, 196) { $g.DrawLine($tahta, 58, $y, 84, $y); $g.DrawLine($tahta, 172, $y, 198, $y) }

# Samanlık kapağı
$g.FillPath((New-Object Drawing.SolidBrush $koyu), (RoundRect 114 90 28 26 6))

# Kapı
$g.FillPath((New-Object Drawing.SolidBrush $koyu), (RoundRect 90 138 76 74 14))
$g.FillRectangle((New-Object Drawing.SolidBrush $koyu), 90, 170, 76, 42)

# Kapıdaki indirme oku (kehribar)
$ok = New-Object Drawing.Pen (C '#fbbf24'), 12
$ok.StartCap = 'Round'; $ok.EndCap = 'Round'; $ok.LineJoin = 'Round'
$g.DrawLine($ok, 128, 152, 128, 194)
$g.DrawLines($ok, [Drawing.Point[]]@((New-Object Drawing.Point 110, 178), (New-Object Drawing.Point 128, 196), (New-Object Drawing.Point 146, 178)))

$bmp.Save((Join-Path $Out "icon.png"), [Drawing.Imaging.ImageFormat]::Png)

# Çok boyutlu ICO (PNG gömülü)
$boyutlar = 256, 128, 64, 48, 32, 24, 16
$pngler = foreach ($b in $boyutlar) {
    $k = New-Object Drawing.Bitmap $b, $b
    $kg = [Drawing.Graphics]::FromImage($k)
    $kg.InterpolationMode = 'HighQualityBicubic'; $kg.SmoothingMode = 'AntiAlias'; $kg.PixelOffsetMode = 'HighQuality'
    $kg.DrawImage($bmp, 0, 0, $b, $b)
    $ms = New-Object IO.MemoryStream
    $k.Save($ms, [Drawing.Imaging.ImageFormat]::Png)
    , $ms.ToArray()
}
$fs = [IO.File]::Create((Join-Path $Out "icon.ico"))
$w = New-Object IO.BinaryWriter $fs
$w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]$boyutlar.Count)
$ofs = 6 + 16 * $boyutlar.Count
for ($i = 0; $i -lt $boyutlar.Count; $i++) {
    $b = $boyutlar[$i]; $wh = if ($b -ge 256) { 0 } else { $b }
    $w.Write([byte]$wh); $w.Write([byte]$wh); $w.Write([byte]0); $w.Write([byte]0)
    $w.Write([uint16]1); $w.Write([uint16]32)
    $w.Write([uint32]$pngler[$i].Length); $w.Write([uint32]$ofs)
    $ofs += $pngler[$i].Length
}
foreach ($p in $pngler) { $w.Write($p) }
$w.Close()
Write-Host "icon.png ve icon.ico olusturuldu: $Out"
