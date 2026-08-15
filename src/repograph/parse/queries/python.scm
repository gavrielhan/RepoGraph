; repograph extraction query: Python
; Capture convention documented in parse/engine.py.

; ---- definitions -----------------------------------------------------------

(function_definition
  name: (identifier) @def.name
  parameters: (parameters) @def.params
  body: (block . (string) @def.doc ?)) @def.function

(class_definition
  name: (identifier) @def.name
  body: (block . (string) @def.doc ?)) @def.class

; ---- inheritance -----------------------------------------------------------

(class_definition
  superclasses: (argument_list [(identifier) (attribute)] @inherit.name)) @inherit

; ---- calls -----------------------------------------------------------------

(call function: (identifier) @call.name) @call
(call function: (attribute) @call.name) @call

; ---- imports ---------------------------------------------------------------

(import_statement name: (dotted_name) @import.module) @import
(import_statement name: (aliased_import name: (dotted_name) @import.module)) @import
(import_from_statement module_name: (dotted_name) @import.module) @import
(import_from_statement module_name: (relative_import) @import.module) @import
(import_from_statement
  module_name: (dotted_name) @import.module
  name: (dotted_name) @import.name) @import
(import_from_statement
  module_name: (dotted_name) @import.module
  name: (aliased_import name: (dotted_name) @import.name)) @import

; ---- data writes -----------------------------------------------------------
; method-style: df.to_parquet("t"), spark_df.write.saveAsTable("t"), ...

(call
  function: (attribute attribute: (identifier) @_wfn)
  arguments: (argument_list (string) @data.write.target)
  (#any-of? @_wfn
    "to_parquet" "to_csv" "to_sql" "to_json" "to_feather" "to_hdf"
    "to_pickle" "to_delta" "to_table" "saveAsTable" "save_as_table"
    "insertInto" "insert_into" "write_table" "write_parquet" "write_csv"
    "createOrReplaceTempView" "create_or_replace_temp_view")) @data.write

; function-style: write_parquet("t", df) after a bare import
(call
  function: (identifier) @_wfn2
  arguments: (argument_list (string) @data.write.target)
  (#any-of? @_wfn2 "write_table" "write_parquet" "write_csv" "save_as_table")) @data.write

; spark writer chain: df.write.parquet("t"), df.write.format(...).save("t")
(call
  function: (attribute
    object: (attribute attribute: (identifier) @_wobj)
    attribute: (identifier))
  arguments: (argument_list . (string) @data.write.target)
  (#eq? @_wobj "write")) @data.write

; ---- data reads ------------------------------------------------------------

(call
  function: (attribute attribute: (identifier) @_rfn)
  arguments: (argument_list (string) @data.read.target)
  (#any-of? @_rfn
    "read_parquet" "read_csv" "read_json" "read_sql" "read_sql_table"
    "read_table" "read_feather" "read_pickle" "read_delta" "read_files"
    "table" "load")) @data.read

(call
  function: (identifier) @_rfn2
  arguments: (argument_list (string) @data.read.target)
  (#any-of? @_rfn2 "read_parquet" "read_csv" "read_json" "read_table")) @data.read

; spark reader chain: spark.read.parquet("t")
(call
  function: (attribute
    object: (attribute attribute: (identifier) @_robj)
    attribute: (identifier))
  arguments: (argument_list . (string) @data.read.target)
  (#eq? @_robj "read")) @data.read
