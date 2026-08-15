; repograph extraction query: Bash

; ---- definitions -----------------------------------------------------------

(function_definition
  name: (word) @def.name) @def.function

; ---- calls -----------------------------------------------------------------
; every invoked command name; resolver matches shell functions and scripts

(command name: (command_name (word) @call.name)) @call
