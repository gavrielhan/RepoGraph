; repograph extraction query: Scala

; ---- definitions -----------------------------------------------------------

(function_definition
  name: (identifier) @def.name
  parameters: (parameters) @def.params) @def.function

(function_definition
  name: (identifier) @def.name) @def.function

(class_definition
  name: (identifier) @def.name) @def.class

(object_definition
  name: (identifier) @def.name) @def.class

(trait_definition
  name: (identifier) @def.name) @def.class

; ---- inheritance -----------------------------------------------------------

(class_definition
  extend: (extends_clause type: (type_identifier) @inherit.name)) @inherit

(class_definition
  extend: (extends_clause type: (generic_type (type_identifier) @inherit.name))) @inherit

(trait_definition
  extend: (extends_clause type: (type_identifier) @inherit.name)) @inherit

(object_definition
  extend: (extends_clause type: (type_identifier) @inherit.name)) @inherit

; ---- calls -----------------------------------------------------------------

(call_expression function: (identifier) @call.name) @call
(call_expression function: (field_expression) @call.name) @call

; ---- imports ---------------------------------------------------------------
; the whole statement is captured; the engine strips the leading keyword
; when @import.module is the same node as @import

(import_declaration) @import @import.module

; ---- data writes (Spark) ---------------------------------------------------

(call_expression
  function: (field_expression field: (identifier) @_wfn)
  arguments: (arguments (string) @data.write.target)
  (#any-of? @_wfn "saveAsTable" "insertInto")) @data.write

(call_expression
  function: (field_expression
    value: (field_expression field: (identifier) @_wobj)
    field: (identifier))
  arguments: (arguments (string) @data.write.target)
  (#eq? @_wobj "write")) @data.write

; ---- data reads (Spark) ----------------------------------------------------

(call_expression
  function: (field_expression field: (identifier) @_rfn)
  arguments: (arguments (string) @data.read.target)
  (#eq? @_rfn "table")) @data.read

(call_expression
  function: (field_expression
    value: (field_expression field: (identifier) @_robj)
    field: (identifier))
  arguments: (arguments (string) @data.read.target)
  (#eq? @_robj "read")) @data.read
