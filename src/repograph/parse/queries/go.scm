; repograph extraction query: Go

; ---- definitions -----------------------------------------------------------

(function_declaration
  name: (identifier) @def.name
  parameters: (parameter_list) @def.params) @def.function

(method_declaration
  name: (field_identifier) @def.name
  parameters: (parameter_list) @def.params) @def.method

(type_declaration
  (type_spec name: (type_identifier) @def.name type: (struct_type))) @def.class

(type_declaration
  (type_spec name: (type_identifier) @def.name type: (interface_type))) @def.class

; ---- calls -----------------------------------------------------------------

(call_expression function: (identifier) @call.name) @call
(call_expression function: (selector_expression) @call.name) @call

; ---- imports ---------------------------------------------------------------

(import_spec path: (interpreted_string_literal) @import.module) @import
