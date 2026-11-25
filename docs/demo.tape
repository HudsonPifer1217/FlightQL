# Records docs/demo.gif.  Run from the repo root:  vhs docs/demo.tape
# Each recording makes one real API call (~$0.02 with Opus).

Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1300
Set Height 820
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

# Setup happens off-camera.
Hide
Type "source .venv/bin/activate && export PS1='$ ' && clear"
Enter
Sleep 1s
Show

Type `python -m flightql "Which 5 airports had the worst average arrival delays in 2024?" --rows 5`
Sleep 600ms
Enter

# Wait for the model + query; the run ends with the cost line.
Sleep 16s
# Hold the final frame so viewers can read it.
Sleep 5s
