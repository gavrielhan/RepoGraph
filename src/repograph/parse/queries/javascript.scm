; repograph extraction query: JavaScript / JSX

; ---- definitions -----------------------------------------------------------

(function_declaration
  name: (identifier) @def.name
  parameters: (formal_parameters) @def.params) @def.function

(method_definition
  name: (property_identifier) @def.name
  parameters: (formal_parameters) @def.params) @def.method

(class_declaration
  name: (identifier) @def.name) @def.class

; const f = (a, b) => ...  /  const f = function (a, b) {...}
(variable_declarator
  name: (identifier) @def.name
  value: (arrow_function parameters: (formal_parameters) @def.params)) @def.function

(variable_declarator
  name: (identifier) @def.name
  value: (function_expression parameters: (formal_parameters) @def.params)) @def.function

; ---- inheritance -----------------------------------------------------------

(class_declaration
  (class_heritage (identifier) @inherit.name)) @inherit

; ---- calls -----------------------------------------------------------------

(call_expression function: (identifier) @call.name) @call
(call_expression function: (member_expression) @call.name) @call

; ---- imports ---------------------------------------------------------------

(import_statement source: (string) @import.module) @import

(call_expression
  function: (identifier) @_req
  arguments: (arguments (string) @import.module)
  (#eq? @_req "require")) @import
