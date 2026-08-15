; repograph extraction query: SQL (derekstride/tree-sitter-sql grammar)
; SQL files produce data edges directly: tables written / tables read.

; ---- writes ----------------------------------------------------------------

(create_table (object_reference) @data.write.target) @data.write
(create_view (object_reference) @data.write.target) @data.write
(insert (object_reference) @data.write.target) @data.write

; ---- reads -----------------------------------------------------------------

(from (relation (object_reference) @data.read.target)) @data.read
(join (relation (object_reference) @data.read.target)) @data.read
