; repograph extraction query: Java

; ---- definitions -----------------------------------------------------------

(method_declaration
  name: (identifier) @def.name
  parameters: (formal_parameters) @def.params) @def.method

(constructor_declaration
  name: (identifier) @def.name
  parameters: (formal_parameters) @def.params) @def.method

(class_declaration
  name: (identifier) @def.name) @def.class

(interface_declaration
  name: (identifier) @def.name) @def.class

(enum_declaration
  name: (identifier) @def.name) @def.class

; ---- inheritance -----------------------------------------------------------

(class_declaration
  superclass: (superclass (type_identifier) @inherit.name)) @inherit

(class_declaration
  interfaces: (super_interfaces (type_list (type_identifier) @inherit.name))) @inherit

(interface_declaration
  (extends_interfaces (type_list (type_identifier) @inherit.name))) @inherit

; ---- calls -----------------------------------------------------------------

(method_invocation name: (identifier) @call.name) @call
(object_creation_expression type: (type_identifier) @call.name) @call

; ---- imports ---------------------------------------------------------------

(import_declaration (scoped_identifier) @import.module) @import
