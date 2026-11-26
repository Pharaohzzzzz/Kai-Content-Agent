param(
    [Parameter(Mandatory = $true)][string]$Request,
    [Parameter(Mandatory = $true)][string]$Directory
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$job = Get-Content -LiteralPath $Request -Raw -Encoding UTF8 | ConvertFrom-Json
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice($job.voice)
    if ($synth.Voice.Culture.Name -notlike 'zh-*') {
        throw "The selected Windows voice is not Chinese."
    }
    $synth.Rate = $job.rate
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        48000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    for ($index = 0; $index -lt $job.parts.Count; $index++) {
        $name = 'tts-{0:D4}.wav' -f $index
        $synth.SetOutputToWaveFile((Join-Path $Directory $name), $format)
        $synth.Speak([string]$job.parts[$index])
        $synth.SetOutputToNull()
    }
    $synth.Voice.Name
} finally {
    $synth.Dispose()
}
