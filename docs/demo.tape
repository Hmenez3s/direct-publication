Output docs/demo.gif

Require uv
Require make
Require forge
Require anvil

Set Shell "bash"
Set FontSize 20
Set Width 1040
Set Height 900
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set PlaybackSpeed 3

Hide
Type "make build-contract && clear"
Enter
Wait
Show

Type "uv run python -m demo.capture"
Sleep 600ms
Enter
Wait+Screen@240s /Capture verified/
Sleep 15s
