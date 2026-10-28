' Start the server with no window at all.
'
' This is the file to put in the Startup folder. WScript.Shell.Run's second
' argument is the window style, and 0 means hidden -- which is why
' run-server.bat runs uvicorn in its own console instead of opening a new one:
' a window opened by `start` would not be covered by this.
'
' Nothing is lost by hiding it:
'   data\logs\<time>_LOG.txt       everything the application logs
'   data\logs\<time>_CONSOLE.txt   everything uvicorn itself prints, which is
'                                   where a failure to start shows up --
'                                   "address already in use" and Python
'                                   tracebacks are written before, or instead
'                                   of, any logging, so they never reach the
'                                   application log.
'
' One console file per run, not a fixed name: the running server holds its
' stdout open on that file, so a second launch cannot even truncate it, and
' the redirect then fails before run-server.bat gets a chance to say "already
' running". Measured -- "the process cannot access the file ... being used by
' another process".
'
' status.bat says whether it worked. stop.bat stops it.

Function P2(n)
  P2 = Right("0" & n, 2)
End Function

Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FolderExists(here & "\data") Then fso.CreateFolder here & "\data"
If Not fso.FolderExists(here & "\data\logs") Then fso.CreateFolder here & "\data\logs"

d = Now
stamp = Year(d) & "-" & P2(Month(d)) & "-" & P2(Day(d)) & "_" & _
        P2(Hour(d)) & P2(Minute(d))

Set sh = CreateObject("WScript.Shell")

sh.Run "cmd /c """"" & here & "\run-server.bat"" > """ & here & _
       "\data\logs\" & stamp & "_CONSOLE.txt"" 2>&1""", 0, False
