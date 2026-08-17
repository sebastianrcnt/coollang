;; ==========================================================================
;; cool0c.wat -- the cool0 compiler, in WebAssembly text
;;
;; This is NOT an independent implementation. It is a transcription of
;; src/cool0/cool0c.cool0, function by function, in the same order. Every
;; function here corresponds to exactly one function there. When the two
;; disagree, the transcription is what is wrong.
;;
;; That is the whole point of the plan: the design was already checked when
;; cool0c.cool0 was checked against cool0.py, so only copying mistakes are
;; left, and byte parity catches those.
;;
;; ------------------------------------------------------------------------
;; The one place this is not literal
;;
;; cool0c.cool0 keeps every table in a slice, so every index it writes is
;; bounds-checked by the code cool0 emits. This file loads directly instead.
;; The milestone is `B == C == P`, not `A == P` -- A only has to *behave*
;; like the compiler, and a check that never fires changes no behaviour. Every
;; arena in cool0c.cool0 is sized from an exact count taken in count_nodes, so
;; no index here can leave its arena.
;;
;; The cost of that shortcut is real and worth stating: if an arena ever did
;; overflow, the cool0-compiled compiler would trap and this one would corrupt
;; memory instead. The checks are the source's safety net, not the algorithm.
;; ------------------------------------------------------------------------
;;
;; Conventions used throughout:
;;
;;   for COND { BODY }        (block $brk (loop $cont
;;                              (br_if $brk (i32.eqz COND))
;;                              (block $cnt BODY)
;;                              (br $cont)))
;;   break                    (br $brk)         innermost wins in wat
;;   continue                 (br $cnt)
;;   a && b                   (if (result i32) a (then b) (else (i32.const 0)))
;;   a || b                   (if (result i32) a (then (i32.const 1)) (else b))
;;   !a                       (i32.eqz a)
;;   -a                       (i32.sub (i32.const 0) a)
;;   match E { ... }          (block $m (block $arm ... (br $m)) ... )
;;
;; `c` is the compiler context, passed as `&mut Ctx` everywhere. Its fields are
;; reached by offset; the table is in the struct block of cool0c.cool0 and is
;; repeated in the accessors below so this file can be read on its own.
;;
;; Named constants are inlined as numbers -- wat has none. The table is
;; spec/language.md plus the const block at the top of cool0c.cool0.
;;
;; String literals live in one data segment at 0x1000000, laid out in first
;; appearance order, exactly as cool0c.cool0 lays out its own.
;; ==========================================================================

(module
  (memory (export "memory") 512 512)
  (global $sp (mut i32) (i32.const 0x2000000))

  ;; ======================================================================
  ;; Ctx field offsets (struct Ctx in cool0c.cool0)
  ;;
  ;;    0 src.ptr     8 pos       12 line      16 col
  ;;   20 toks.ptr   28 ntok      32 nodes.ptr  40 nnode
  ;;   44 ti         48 edepth    52 bdepth     56 decls
  ;;   60 types.ptr  68 nty       72 fields.ptr 80 nfield
  ;;   84 structs    92 nstruct   96 variants  104 nvariant
  ;;  108 enums     116 nenum    120 consts    128 nconst
  ;;  132 params    140 nparam   144 fns       152 nfn
  ;;  156 locals    164 nlocal   168 strs      176 nstr
  ;;  180 taken     188 ntaken   192 tynames   200 ntyname
  ;;  204 rodata    208 cety     212 scope     220 scope_n
  ;;  224 marks     232 marks_n  236 curfn     240 loopd
  ;;  244 unsafed   248 place_mut 252 place_root 256 nfns
  ;;  260 wb        264 ws       268 sigs      276 nsigs
  ;;  280 ntemp     284 free     292 free_n    296 ctrl
  ;;  304 ctrl_n    308 efn      312 heap      316 err
  ;;  320 out
  ;;
  ;; A slice field occupies two words: ptr at off, len at off+4.
  ;; ======================================================================

  ;; --- context accessors --------------------------------------------------

  (func $cg (param $c i32) (param $off i32) (result i32)
    (i32.load (i32.add (local.get $c) (local.get $off))))

  (func $cs (param $c i32) (param $off i32) (param $v i32)
    (i32.store (i32.add (local.get $c) (local.get $off)) (local.get $v)))

  ;; --- raw memory ---------------------------------------------------------

  (func $ldb (export "ldb") (param $a i32) (result i32)
    (i32.load8_u (local.get $a)))

  (func $stb (export "stb") (param $a i32) (param $v i32)
    (i32.store8 (local.get $a) (local.get $v)))

  (func $align4 (export "align4") (param $v i32) (result i32)
    (i32.mul (i32.div_u (i32.add (local.get $v) (i32.const 3)) (i32.const 4))
             (i32.const 4)))

  ;; --- source bytes -------------------------------------------------------
  ;; c.^.src[i] -- src.ptr is at offset 0, src.len at 4

  (func $src_at (param $c i32) (param $i i32) (result i32)
    (i32.load8_u (i32.add (call $cg (local.get $c) (i32.const 0)) (local.get $i))))

  (func $src_len (param $c i32) (result i32)
    (call $cg (local.get $c) (i32.const 4)))

  ;; --- output buffer ------------------------------------------------------

  (func $out_reset (export "out_reset") (param $c i32)
    (call $cs (local.get $c) (i32.const 320) (i32.const 0x1010000)))

  (func $put_ch (export "put_ch") (param $c i32) (param $ch i32)
    (call $stb (call $cg (local.get $c) (i32.const 320)) (local.get $ch))
    (call $cs (local.get $c) (i32.const 320)
              (i32.add (call $cg (local.get $c) (i32.const 320)) (i32.const 1))))

  ;; put_str takes a slice, so in wasm it takes (ptr, len)
  (func $put_str (export "put_str") (param $c i32) (param $sp i32) (param $sl i32)
    (local $i i32)
    (local.set $i (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $i) (local.get $sl))))
      (block $cnt
        (call $put_ch (local.get $c)
                      (i32.load8_u (i32.add (local.get $sp) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $cont))))

  (func $put_src (export "put_src") (param $c i32) (param $start i32) (param $len i32)
    (local $i i32)
    (local.set $i (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $i) (local.get $len))))
      (block $cnt
        (call $put_ch (local.get $c)
                      (call $src_at (local.get $c)
                                    (i32.add (local.get $start) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $cont))))

  (func $put_num (export "put_num") (param $c i32) (param $v i32)
    (if (i32.ge_u (local.get $v) (i32.const 10))
        (then (call $put_num (local.get $c) (i32.div_u (local.get $v) (i32.const 10)))))
    (call $put_ch (local.get $c)
                  (i32.add (i32.const 48) (i32.rem_u (local.get $v) (i32.const 10)))))

  ;; --- diagnostics --------------------------------------------------------

  (func $failed (export "failed") (param $c i32) (result i32)
    (i32.ne (call $cg (local.get $c) (i32.const 316)) (i32.const 0)))

  (func $err_open (export "err_open") (param $c i32) (param $line i32) (param $col i32)
        (result i32)
    (if (call $failed (local.get $c)) (then (return (i32.const 0))))
    (call $cs (local.get $c) (i32.const 316) (i32.const 1))
    (call $out_reset (local.get $c))
    (call $put_num (local.get $c) (local.get $line))
    (call $put_ch (local.get $c) (i32.const 58))
    (call $put_num (local.get $c) (local.get $col))
    (call $put_ch (local.get $c) (i32.const 58))
    (call $put_ch (local.get $c) (i32.const 32))
    (i32.const 1))

  (func $err_end (export "err_end") (param $c i32)
    (call $put_ch (local.get $c) (i32.const 10)))

  (func $err_msg (export "err_msg") (param $c i32) (param $line i32) (param $col i32)
        (param $sp i32) (param $sl i32)
    (if (call $err_open (local.get $c) (local.get $line) (local.get $col))
        (then (call $put_str (local.get $c) (local.get $sp) (local.get $sl))
              (call $err_end (local.get $c)))))

  ;; ======================================================================
  ;; Tokens
  ;;
  ;; struct Token is 28 bytes: kind 0, start 4, len 8, value 12, line 16,
  ;; col 20, aux 24. The arena is c.toks (ptr at 20).
  ;; ======================================================================

  (func $tok (param $c i32) (param $i i32) (result i32)
    (i32.add (call $cg (local.get $c) (i32.const 20))
             (i32.mul (local.get $i) (i32.const 28))))

  (func $tk (param $c i32) (param $i i32) (param $f i32) (result i32)
    (i32.load (i32.add (call $tok (local.get $c) (local.get $i)) (local.get $f))))

  ;; ======================================================================
  ;; Lexer
  ;; ======================================================================

  (func $is_alpha (export "is_alpha") (param $b i32) (result i32)
    (if (result i32)
        (if (result i32) (i32.ge_u (local.get $b) (i32.const 65))
            (then (i32.le_u (local.get $b) (i32.const 90)))
            (else (i32.const 0)))
        (then (i32.const 1))
        (else (if (result i32) (i32.ge_u (local.get $b) (i32.const 97))
                  (then (i32.le_u (local.get $b) (i32.const 122)))
                  (else (i32.const 0))))))

  (func $is_digit (export "is_digit") (param $b i32) (result i32)
    (if (result i32) (i32.ge_u (local.get $b) (i32.const 48))
        (then (i32.le_u (local.get $b) (i32.const 57)))
        (else (i32.const 0))))

  (func $is_ident_start (export "is_ident_start") (param $b i32) (result i32)
    (if (result i32) (call $is_alpha (local.get $b))
        (then (i32.const 1))
        (else (i32.eq (local.get $b) (i32.const 95)))))

  (func $is_ident_cont (export "is_ident_cont") (param $b i32) (result i32)
    (if (result i32)
        (if (result i32) (call $is_alpha (local.get $b))
            (then (i32.const 1))
            (else (call $is_digit (local.get $b))))
        (then (i32.const 1))
        (else (i32.eq (local.get $b) (i32.const 95)))))

  (func $at (export "at") (param $c i32) (param $i i32) (result i32)
    (call $src_at (local.get $c) (local.get $i)))

  (func $adv (export "adv") (param $c i32) (param $k i32)
    (local $t i32) (local $p i32)
    (local.set $t (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $t) (local.get $k))))
      (block $cnt
        (local.set $p (call $cg (local.get $c) (i32.const 8)))
        (if (i32.eq (call $src_at (local.get $c) (local.get $p)) (i32.const 10))
            (then (call $cs (local.get $c) (i32.const 12)
                            (i32.add (call $cg (local.get $c) (i32.const 12))
                                     (i32.const 1)))
                  (call $cs (local.get $c) (i32.const 16) (i32.const 1)))
            (else (call $cs (local.get $c) (i32.const 16)
                            (i32.add (call $cg (local.get $c) (i32.const 16))
                                     (i32.const 1)))))
        (call $cs (local.get $c) (i32.const 8) (i32.add (local.get $p) (i32.const 1))))
      (local.set $t (i32.add (local.get $t) (i32.const 1)))
      (br $cont))))

  (func $src_eq (export "src_eq") (param $c i32) (param $start i32) (param $len i32)
        (param $sp i32) (param $sl i32) (result i32)
    (local $i i32)
    (if (i32.ne (local.get $len) (local.get $sl)) (then (return (i32.const 0))))
    (local.set $i (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $i) (local.get $len))))
      (block $cnt
        (if (i32.ne (call $src_at (local.get $c) (i32.add (local.get $start) (local.get $i)))
                    (i32.load8_u (i32.add (local.get $sp) (local.get $i))))
            (then (return (i32.const 0)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $cont)))
    (i32.const 1))

  (func $src_has (export "src_has") (param $c i32) (param $i i32)
        (param $sp i32) (param $sl i32) (result i32)
    (local $k i32)
    (if (i32.gt_u (i32.add (local.get $i) (local.get $sl)) (call $src_len (local.get $c)))
        (then (return (i32.const 0))))
    (local.set $k (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $k) (local.get $sl))))
      (block $cnt
        (if (i32.ne (call $src_at (local.get $c) (i32.add (local.get $i) (local.get $k)))
                    (i32.load8_u (i32.add (local.get $sp) (local.get $k))))
            (then (return (i32.const 0)))))
      (local.set $k (i32.add (local.get $k) (i32.const 1)))
      (br $cont)))
    (i32.const 1))

  ;; keyword ids -- the order language.md S2 lists them in
  (func $kw_id (export "kw_id") (param $c i32) (param $start i32) (param $len i32)
        (result i32)
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000000) (i32.const 2)) (then (return (i32.const 0))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000002) (i32.const 6)) (then (return (i32.const 1))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000008) (i32.const 4)) (then (return (i32.const 2))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x100000C) (i32.const 5)) (then (return (i32.const 3))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000011) (i32.const 3)) (then (return (i32.const 4))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000014) (i32.const 3)) (then (return (i32.const 5))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000017) (i32.const 2)) (then (return (i32.const 6))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000019) (i32.const 4)) (then (return (i32.const 7))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x100001D) (i32.const 3)) (then (return (i32.const 8))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000020) (i32.const 5)) (then (return (i32.const 9))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000025) (i32.const 8)) (then (return (i32.const 10))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x100002D) (i32.const 6)) (then (return (i32.const 11))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000033) (i32.const 5)) (then (return (i32.const 12))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000038) (i32.const 6)) (then (return (i32.const 13))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x100003E) (i32.const 2)) (then (return (i32.const 14))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000040) (i32.const 4)) (then (return (i32.const 15))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000044) (i32.const 5)) (then (return (i32.const 16))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000049) (i32.const 5)) (then (return (i32.const 17))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x100004E) (i32.const 9)) (then (return (i32.const 18))))
    (if (call $src_eq (local.get $c) (local.get $start) (local.get $len)
                      (i32.const 0x1000057) (i32.const 6)) (then (return (i32.const 19))))
    (i32.const -1))

  ;; longest match (language.md S2)
  (func $punct_id (export "punct_id") (param $c i32) (param $i i32) (result i32)
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100005D) (i32.const 3))
        (then (return (i32.const 0))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000060) (i32.const 3))
        (then (return (i32.const 1))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000063) (i32.const 2))
        (then (return (i32.const 2))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000065) (i32.const 2))
        (then (return (i32.const 3))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000067) (i32.const 2))
        (then (return (i32.const 4))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000069) (i32.const 2))
        (then (return (i32.const 5))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100006B) (i32.const 2))
        (then (return (i32.const 6))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100006D) (i32.const 2))
        (then (return (i32.const 7))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100006F) (i32.const 2))
        (then (return (i32.const 8))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000071) (i32.const 2))
        (then (return (i32.const 9))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000073) (i32.const 2))
        (then (return (i32.const 10))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000075) (i32.const 2))
        (then (return (i32.const 11))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000077) (i32.const 2))
        (then (return (i32.const 12))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000079) (i32.const 2))
        (then (return (i32.const 13))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100007B) (i32.const 2))
        (then (return (i32.const 14))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100007D) (i32.const 2))
        (then (return (i32.const 15))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100007F) (i32.const 2))
        (then (return (i32.const 16))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000081) (i32.const 2))
        (then (return (i32.const 17))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000083) (i32.const 2))
        (then (return (i32.const 18))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000085) (i32.const 2))
        (then (return (i32.const 19))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000087) (i32.const 1))
        (then (return (i32.const 20))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000088) (i32.const 1))
        (then (return (i32.const 21))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000089) (i32.const 1))
        (then (return (i32.const 22))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008A) (i32.const 1))
        (then (return (i32.const 23))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008B) (i32.const 1))
        (then (return (i32.const 24))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008C) (i32.const 1))
        (then (return (i32.const 25))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008D) (i32.const 1))
        (then (return (i32.const 26))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008E) (i32.const 1))
        (then (return (i32.const 27))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100008F) (i32.const 1))
        (then (return (i32.const 28))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000090) (i32.const 1))
        (then (return (i32.const 29))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000091) (i32.const 1))
        (then (return (i32.const 30))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000092) (i32.const 1))
        (then (return (i32.const 31))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000093) (i32.const 1))
        (then (return (i32.const 32))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000094) (i32.const 1))
        (then (return (i32.const 33))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000095) (i32.const 1))
        (then (return (i32.const 34))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000096) (i32.const 1))
        (then (return (i32.const 35))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000097) (i32.const 1))
        (then (return (i32.const 36))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000098) (i32.const 1))
        (then (return (i32.const 37))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x1000099) (i32.const 1))
        (then (return (i32.const 38))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100009A) (i32.const 1))
        (then (return (i32.const 39))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100009B) (i32.const 1))
        (then (return (i32.const 40))))
    (if (call $src_has (local.get $c) (local.get $i) (i32.const 0x100009C) (i32.const 1))
        (then (return (i32.const 41))))
    (i32.const -1))

  (func $punct_len (export "punct_len") (param $id i32) (result i32)
    (if (i32.le_u (local.get $id) (i32.const 1)) (then (return (i32.const 3))))
    (if (i32.le_u (local.get $id) (i32.const 19)) (then (return (i32.const 2))))
    (i32.const 1))

  (func $push_tok (export "push_tok") (param $c i32) (param $kind i32) (param $start i32)
        (param $len i32) (param $value i32) (param $line i32) (param $col i32)
        (param $aux i32)
    (local $n i32) (local $t i32)
    (local.set $n (call $cg (local.get $c) (i32.const 28)))
    (local.set $t (call $tok (local.get $c) (local.get $n)))
    (i32.store (local.get $t) (local.get $kind))
    (i32.store (i32.add (local.get $t) (i32.const 4)) (local.get $start))
    (i32.store (i32.add (local.get $t) (i32.const 8)) (local.get $len))
    (i32.store (i32.add (local.get $t) (i32.const 12)) (local.get $value))
    (i32.store (i32.add (local.get $t) (i32.const 16)) (local.get $line))
    (i32.store (i32.add (local.get $t) (i32.const 20)) (local.get $col))
    (i32.store (i32.add (local.get $t) (i32.const 24)) (local.get $aux))
    (call $cs (local.get $c) (i32.const 28) (i32.add (local.get $n) (i32.const 1))))

  ;; One escape. Strings allow one more than chars do (language.md S2).
  (func $escape_of (export "escape_of") (param $e i32) (param $in_string i32)
        (result i32)
    (if (i32.eq (local.get $e) (i32.const 110)) (then (return (i32.const 10))))
    (if (i32.eq (local.get $e) (i32.const 116)) (then (return (i32.const 9))))
    (if (i32.eq (local.get $e) (i32.const 114)) (then (return (i32.const 13))))
    (if (i32.eq (local.get $e) (i32.const 48)) (then (return (i32.const 0))))
    (if (i32.eq (local.get $e) (i32.const 92)) (then (return (i32.const 92))))
    (if (i32.eq (local.get $e) (i32.const 39)) (then (return (i32.const 39))))
    (if (if (result i32) (local.get $in_string)
            (then (i32.eq (local.get $e) (i32.const 34)))
            (else (i32.const 0)))
        (then (return (i32.const 34))))
    (i32.const -1))

  (func $lex_ident (export "lex_ident") (param $c i32)
    (local $sl i32) (local $sc i32) (local $i i32) (local $n i32) (local $j i32)
    (local $id i32)
    (local.set $sl (call $cg (local.get $c) (i32.const 12)))
    (local.set $sc (call $cg (local.get $c) (i32.const 16)))
    (local.set $i (call $cg (local.get $c) (i32.const 8)))
    (local.set $n (call $src_len (local.get $c)))
    (local.set $j (local.get $i))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (if (result i32) (i32.lt_u (local.get $j) (local.get $n))
                               (then (call $is_ident_cont
                                           (call $src_at (local.get $c) (local.get $j))))
                               (else (i32.const 0)))))
      (block $cnt (local.set $j (i32.add (local.get $j) (i32.const 1))))
      (br $cont)))
    (local.set $id (call $kw_id (local.get $c) (local.get $i)
                                (i32.sub (local.get $j) (local.get $i))))
    (call $adv (local.get $c) (i32.sub (local.get $j) (local.get $i)))
    (if (i32.eq (local.get $id) (i32.const -1))
        (then (call $push_tok (local.get $c) (i32.const 1) (local.get $i)
                              (i32.sub (local.get $j) (local.get $i)) (i32.const 0)
                              (local.get $sl) (local.get $sc) (i32.const 0)))
        (else (call $push_tok (local.get $c) (i32.const 5) (local.get $i)
                              (i32.sub (local.get $j) (local.get $i)) (local.get $id)
                              (local.get $sl) (local.get $sc) (i32.const 0)))))

  (func $digit_val (export "digit_val") (param $ch i32) (param $base i32) (result i32)
    (if (call $is_digit (local.get $ch))
        (then (if (i32.lt_u (i32.sub (local.get $ch) (i32.const 48)) (local.get $base))
                  (then (return (i32.sub (local.get $ch) (i32.const 48)))))
              (return (i32.const -1))))
    (if (i32.eq (local.get $base) (i32.const 16))
        (then (if (if (result i32) (i32.ge_u (local.get $ch) (i32.const 97))
                      (then (i32.le_u (local.get $ch) (i32.const 102)))
                      (else (i32.const 0)))
                  (then (return (i32.add (i32.sub (local.get $ch) (i32.const 97))
                                         (i32.const 10)))))
              (if (if (result i32) (i32.ge_u (local.get $ch) (i32.const 65))
                      (then (i32.le_u (local.get $ch) (i32.const 70)))
                      (else (i32.const 0)))
                  (then (return (i32.add (i32.sub (local.get $ch) (i32.const 65))
                                         (i32.const 10)))))))
    (i32.const -1))

  (func $lex_number (export "lex_number") (param $c i32)
    (local $sl i32) (local $sc i32) (local $i i32) (local $n i32) (local $base i32)
    (local $j i32) (local $b i32) (local $ndigits i32) (local $value i32)
    (local $over i32) (local $d i32)
    (local.set $sl (call $cg (local.get $c) (i32.const 12)))
    (local.set $sc (call $cg (local.get $c) (i32.const 16)))
    (local.set $i (call $cg (local.get $c) (i32.const 8)))
    (local.set $n (call $src_len (local.get $c)))
    (local.set $base (i32.const 10))
    (local.set $j (local.get $i))
    (if (if (result i32)
            (i32.eq (call $src_at (local.get $c) (local.get $i)) (i32.const 48))
            (then (i32.lt_u (i32.add (local.get $i) (i32.const 1)) (local.get $n)))
            (else (i32.const 0)))
        (then
          (local.set $b (call $src_at (local.get $c)
                                      (i32.add (local.get $i) (i32.const 1))))
          (if (if (result i32) (i32.eq (local.get $b) (i32.const 120))
                  (then (i32.const 1))
                  (else (i32.eq (local.get $b) (i32.const 88))))
              (then (local.set $base (i32.const 16))
                    (local.set $j (i32.add (local.get $i) (i32.const 2)))))
          (if (if (result i32) (i32.eq (local.get $b) (i32.const 98))
                  (then (i32.const 1))
                  (else (i32.eq (local.get $b) (i32.const 66))))
              (then (local.set $base (i32.const 2))
                    (local.set $j (i32.add (local.get $i) (i32.const 2)))))))
    (local.set $ndigits (i32.const 0))
    (local.set $value (i32.const 0))
    (local.set $over (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz (i32.lt_u (local.get $j) (local.get $n))))
      (block $cnt
        (local.set $b (call $src_at (local.get $c) (local.get $j)))
        (if (i32.eq (local.get $b) (i32.const 95))
            (then (local.set $j (i32.add (local.get $j) (i32.const 1))))
            (else
              (local.set $d (call $digit_val (local.get $b) (local.get $base)))
              (br_if $brk (i32.eq (local.get $d) (i32.const -1)))
              ;; detect overflow past 32 bits; the value itself is then meaningless
              (if (i32.gt_u (local.get $value)
                            (i32.div_u (i32.sub (i32.const -1) (local.get $d))
                                       (local.get $base)))
                  (then (local.set $over (i32.const 1))))
              (local.set $value (i32.add (i32.mul (local.get $value) (local.get $base))
                                         (local.get $d)))
              (local.set $ndigits (i32.add (local.get $ndigits) (i32.const 1)))
              (local.set $j (i32.add (local.get $j) (i32.const 1))))))
      (br $cont)))
    (if (i32.eq (local.get $ndigits) (i32.const 0))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x100009D) (i32.const 29))
              (return)))
    (if (if (result i32) (i32.lt_u (local.get $j) (local.get $n))
            (then (call $is_ident_cont (call $src_at (local.get $c) (local.get $j))))
            (else (i32.const 0)))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x10000BA) (i32.const 32))
              (return)))
    (if (local.get $over)
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x10000DA) (i32.const 28))
              (return)))
    (call $adv (local.get $c) (i32.sub (local.get $j) (local.get $i)))
    (call $push_tok (local.get $c) (i32.const 2) (local.get $i)
                    (i32.sub (local.get $j) (local.get $i)) (local.get $value)
                    (local.get $sl) (local.get $sc) (i32.const 0)))

  (func $lex_char (export "lex_char") (param $c i32)
    (local $sl i32) (local $sc i32) (local $start i32) (local $n i32)
    (local $value i32) (local $e i32) (local $el i32) (local $ec i32) (local $end i32)
    (local.set $sl (call $cg (local.get $c) (i32.const 12)))
    (local.set $sc (call $cg (local.get $c) (i32.const 16)))
    (local.set $start (call $cg (local.get $c) (i32.const 8)))
    (local.set $n (call $src_len (local.get $c)))
    (call $adv (local.get $c) (i32.const 1))
    (if (i32.ge_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x10000F6) (i32.const 30))
              (return)))
    (if (i32.eq (call $src_at (local.get $c) (call $cg (local.get $c) (i32.const 8)))
                (i32.const 39))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x1000114) (i32.const 23))
              (return)))
    (local.set $value (i32.const 0))
    (if (i32.eq (call $src_at (local.get $c) (call $cg (local.get $c) (i32.const 8)))
                (i32.const 92))
        (then
          (call $adv (local.get $c) (i32.const 1))
          (if (i32.ge_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
              (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                                   (i32.const 0x10000F6) (i32.const 30))
                    (return)))
          (local.set $e (call $escape_of
                              (call $src_at (local.get $c)
                                    (call $cg (local.get $c) (i32.const 8)))
                              (i32.const 0)))
          (if (i32.eq (local.get $e) (i32.const -1))
              (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                    (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                    (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                   (i32.const 0x100012B) (i32.const 23))
                    (return)))
          (local.set $value (local.get $e))
          (call $adv (local.get $c) (i32.const 1)))
        (else
          (local.set $value (call $src_at (local.get $c)
                                          (call $cg (local.get $c) (i32.const 8))))
          (if (if (result i32) (i32.lt_u (local.get $value) (i32.const 0x20))
                  (then (i32.const 1))
                  (else (i32.gt_u (local.get $value) (i32.const 0x7E))))
              (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                    (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                    (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                   (i32.const 0x1000142) (i32.const 38))
                    (return)))
          (call $adv (local.get $c) (i32.const 1))))
    (if (i32.ge_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x10000F6) (i32.const 30))
              (return)))
    (if (i32.ne (call $src_at (local.get $c) (call $cg (local.get $c) (i32.const 8)))
                (i32.const 39))
        (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                             (i32.const 0x10000F6) (i32.const 30))
              (return)))
    (call $adv (local.get $c) (i32.const 1))
    (local.set $end (call $cg (local.get $c) (i32.const 8)))
    (call $push_tok (local.get $c) (i32.const 3) (local.get $start)
                    (i32.sub (local.get $end) (local.get $start)) (local.get $value)
                    (local.get $sl) (local.get $sc) (i32.const 0)))

  (func $lex_string (export "lex_string") (param $c i32)
    (local $sl i32) (local $sc i32) (local $start i32) (local $n i32) (local $buf i32)
    (local $k i32) (local $b i32) (local $e i32) (local $el i32) (local $ec i32)
    (local $end i32)
    (local.set $sl (call $cg (local.get $c) (i32.const 12)))
    (local.set $sc (call $cg (local.get $c) (i32.const 16)))
    (local.set $start (call $cg (local.get $c) (i32.const 8)))
    (local.set $n (call $src_len (local.get $c)))
    (call $adv (local.get $c) (i32.const 1))
    (local.set $buf (call $cg (local.get $c) (i32.const 312)))
    (local.set $k (i32.const 0))
    (block $brk (loop $cont
      (block $cnt
        (if (i32.ge_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
            (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                                 (i32.const 0x1000168) (i32.const 27))
                  (return)))
        (local.set $b (call $src_at (local.get $c)
                                    (call $cg (local.get $c) (i32.const 8))))
        (if (i32.eq (local.get $b) (i32.const 10))
            (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                                 (i32.const 0x1000168) (i32.const 27))
                  (return)))
        (if (i32.eq (local.get $b) (i32.const 34))
            (then (call $adv (local.get $c) (i32.const 1))
                  (br $brk)))
        (if (i32.eq (local.get $b) (i32.const 92))
            (then
              (call $adv (local.get $c) (i32.const 1))
              (if (i32.ge_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
                  (then (call $err_msg (local.get $c) (local.get $sl) (local.get $sc)
                                       (i32.const 0x1000168) (i32.const 27))
                        (return)))
              (local.set $e (call $escape_of
                                  (call $src_at (local.get $c)
                                        (call $cg (local.get $c) (i32.const 8)))
                                  (i32.const 1)))
              (if (i32.eq (local.get $e) (i32.const -1))
                  (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                        (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                        (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                       (i32.const 0x100012B) (i32.const 23))
                        (return)))
              (call $stb (i32.add (local.get $buf) (local.get $k)) (local.get $e))
              (local.set $k (i32.add (local.get $k) (i32.const 1)))
              (call $adv (local.get $c) (i32.const 1)))
            (else
              (if (if (result i32) (i32.lt_u (local.get $b) (i32.const 0x20))
                      (then (i32.const 1))
                      (else (i32.gt_u (local.get $b) (i32.const 0x7E))))
                  (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                        (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                        (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                       (i32.const 0x1000183) (i32.const 35))
                        (return)))
              (call $stb (i32.add (local.get $buf) (local.get $k)) (local.get $b))
              (local.set $k (i32.add (local.get $k) (i32.const 1)))
              (call $adv (local.get $c) (i32.const 1)))))
      (br $cont)))
    (call $cs (local.get $c) (i32.const 312)
              (call $align4 (i32.add (local.get $buf) (local.get $k))))
    (local.set $end (call $cg (local.get $c) (i32.const 8)))
    (call $push_tok (local.get $c) (i32.const 4) (local.get $start)
                    (i32.sub (local.get $end) (local.get $start)) (local.get $buf)
                    (local.get $sl) (local.get $sc) (local.get $k)))

  (func $lex_comment (export "lex_comment") (param $c i32)
    (local $n i32) (local $b i32) (local $el i32) (local $ec i32)
    (local.set $n (call $src_len (local.get $c)))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz
        (if (result i32) (i32.lt_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
            (then (i32.ne (call $src_at (local.get $c)
                                (call $cg (local.get $c) (i32.const 8)))
                          (i32.const 10)))
            (else (i32.const 0)))))
      (block $cnt
        (local.set $b (call $src_at (local.get $c)
                                    (call $cg (local.get $c) (i32.const 8))))
        (if (if (result i32) (i32.ne (local.get $b) (i32.const 9))
                (then (if (result i32) (i32.ne (local.get $b) (i32.const 13))
                          (then (if (result i32)
                                    (i32.lt_u (local.get $b) (i32.const 0x20))
                                    (then (i32.const 1))
                                    (else (i32.gt_u (local.get $b) (i32.const 0x7E)))))
                          (else (i32.const 0))))
                (else (i32.const 0)))
            (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                  (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                  (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                 (i32.const 0x10001A6) (i32.const 24))
                  (return)))
        (call $adv (local.get $c) (i32.const 1)))
      (br $cont))))

  (func $lex (export "lex") (param $c i32)
    (local $n i32) (local $i i32) (local $b i32) (local $p i32)
    (local $el i32) (local $ec i32) (local $sl i32) (local $sc i32) (local $ep i32)
    (local.set $n (call $src_len (local.get $c)))
    (call $cs (local.get $c) (i32.const 8) (i32.const 0))
    (call $cs (local.get $c) (i32.const 12) (i32.const 1))
    (call $cs (local.get $c) (i32.const 16) (i32.const 1))
    (call $cs (local.get $c) (i32.const 28) (i32.const 0))
    (block $brk (loop $cont
      (br_if $brk (i32.eqz
        (if (result i32) (i32.lt_u (call $cg (local.get $c) (i32.const 8)) (local.get $n))
            (then (i32.eqz (call $failed (local.get $c))))
            (else (i32.const 0)))))
      (block $cnt
        (local.set $i (call $cg (local.get $c) (i32.const 8)))
        (local.set $b (call $src_at (local.get $c) (local.get $i)))
        (if (if (result i32) (i32.ne (local.get $b) (i32.const 9))
                (then (if (result i32) (i32.ne (local.get $b) (i32.const 10))
                          (then (if (result i32) (i32.ne (local.get $b) (i32.const 13))
                                    (then (if (result i32)
                                              (i32.lt_u (local.get $b) (i32.const 0x20))
                                              (then (i32.const 1))
                                              (else (i32.gt_u (local.get $b)
                                                              (i32.const 0x7E)))))
                                    (else (i32.const 0))))
                          (else (i32.const 0))))
                (else (i32.const 0)))
            (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                  (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                  (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                 (i32.const 0x10001A6) (i32.const 24))
                  (return)))
        (if (if (result i32) (i32.eq (local.get $b) (i32.const 9))
                (then (i32.const 1))
                (else (if (result i32) (i32.eq (local.get $b) (i32.const 10))
                          (then (i32.const 1))
                          (else (if (result i32) (i32.eq (local.get $b) (i32.const 13))
                                    (then (i32.const 1))
                                    (else (i32.eq (local.get $b) (i32.const 32))))))))
            (then (call $adv (local.get $c) (i32.const 1))
                  (br $cnt)))
        (if (if (result i32) (i32.eq (local.get $b) (i32.const 47))
                (then (if (result i32)
                          (i32.lt_u (i32.add (local.get $i) (i32.const 1)) (local.get $n))
                          (then (i32.eq (call $src_at (local.get $c)
                                              (i32.add (local.get $i) (i32.const 1)))
                                        (i32.const 47)))
                          (else (i32.const 0))))
                (else (i32.const 0)))
            (then (call $lex_comment (local.get $c))
                  (br $cnt)))
        (if (call $is_ident_start (local.get $b))
            (then (call $lex_ident (local.get $c)) (br $cnt)))
        (if (call $is_digit (local.get $b))
            (then (call $lex_number (local.get $c)) (br $cnt)))
        (if (i32.eq (local.get $b) (i32.const 39))
            (then (call $lex_char (local.get $c)) (br $cnt)))
        (if (i32.eq (local.get $b) (i32.const 34))
            (then (call $lex_string (local.get $c)) (br $cnt)))
        (local.set $p (call $punct_id (local.get $c) (local.get $i)))
        (if (i32.eq (local.get $p) (i32.const -1))
            (then (local.set $el (call $cg (local.get $c) (i32.const 12)))
                  (local.set $ec (call $cg (local.get $c) (i32.const 16)))
                  (call $err_msg (local.get $c) (local.get $el) (local.get $ec)
                                 (i32.const 0x10001BE) (i32.const 20))
                  (return)))
        (local.set $sl (call $cg (local.get $c) (i32.const 12)))
        (local.set $sc (call $cg (local.get $c) (i32.const 16)))
        (call $adv (local.get $c) (call $punct_len (local.get $p)))
        (call $push_tok (local.get $c) (i32.const 6) (local.get $i)
                        (call $punct_len (local.get $p)) (local.get $p)
                        (local.get $sl) (local.get $sc) (i32.const 0)))
      (br $cont)))
    (if (call $failed (local.get $c)) (then (return)))
    (local.set $ep (call $cg (local.get $c) (i32.const 8)))
    (local.set $el (call $cg (local.get $c) (i32.const 12)))
    (local.set $ec (call $cg (local.get $c) (i32.const 16)))
    (call $push_tok (local.get $c) (i32.const 0) (local.get $ep) (i32.const 0)
                    (i32.const 0) (local.get $el) (local.get $ec) (i32.const 0)))

  ;; ======================================================================
  ;; Entry point (implementation.md S7)
  ;;
  ;; Ctx is an aggregate local in cool0c.cool0, so it lives in the shadow
  ;; frame. 324 bytes rounded to 368 -- Counts sits beside it.
  ;; ======================================================================

  (func $compile (export "compile") (param $src_len_in i32) (result i32)
    (local $c i32) (local $heap0 i32) (local $ntok_max i32) (local $tok_bytes i32)
    (global.set $sp (i32.sub (global.get $sp) (i32.const 368)))
    (if (i32.lt_u (global.get $sp) (i32.const 0x1800000)) (then (unreachable)))
    (local.set $c (global.get $sp))

    (local.set $heap0 (i32.add (call $align4 (i32.add (i32.const 0x1000)
                                                      (local.get $src_len_in)))
                               (i32.const 4)))
    (local.set $ntok_max (i32.add (local.get $src_len_in) (i32.const 1)))
    (local.set $tok_bytes (i32.mul (local.get $ntok_max) (i32.const 28)))

    ;; Ctx{ ... } -- written straight into the frame, field by field
    (call $cs (local.get $c) (i32.const 0) (i32.const 0x1000))
    (call $cs (local.get $c) (i32.const 4) (local.get $src_len_in))
    (call $cs (local.get $c) (i32.const 8) (i32.const 0))
    (call $cs (local.get $c) (i32.const 12) (i32.const 1))
    (call $cs (local.get $c) (i32.const 16) (i32.const 1))
    (call $cs (local.get $c) (i32.const 20) (local.get $heap0))
    (call $cs (local.get $c) (i32.const 24) (local.get $ntok_max))
    (call $cs (local.get $c) (i32.const 28) (i32.const 0))
    (call $cs (local.get $c) (i32.const 312)
              (i32.add (local.get $heap0) (local.get $tok_bytes)))
    (call $cs (local.get $c) (i32.const 316) (i32.const 0))
    (call $cs (local.get $c) (i32.const 320) (i32.const 0x1010000))

    (call $lex (local.get $c))

    ;; debug window for the workbench
    (i32.store (i32.const 0x30) (local.get $heap0))
    (i32.store (i32.const 0x2C) (call $cg (local.get $c) (i32.const 28)))
    (i32.store (i32.const 0x14) (call $cg (local.get $c) (i32.const 316)))

    (i32.store (i32.const 0x00) (i32.const 0x1010000))
    (i32.store (i32.const 0x04) (i32.sub (call $cg (local.get $c) (i32.const 320))
                                         (i32.const 0x1010000)))
    (global.set $sp (i32.add (global.get $sp) (i32.const 368)))
    (if (i32.ne (call $cg (local.get $c) (i32.const 316)) (i32.const 0))
        (then (return (i32.const 1))))
    (i32.const 0))

  ;; string literals, first appearance order, exactly as cool0c.cool0 lays out its own
  (data (i32.const 0x1000000)
    "fnstructenumconstletmutifelseforbreakcontinuereturnmatchunsafeastruefalsesliceslice_muto"
    "ffset<<=>>=->=>==!=<=>=&&||<<>>+=-=*=/=%=&=|=^=(){}[],;:.=<>+-*/%&|^!integer literal has"
    " no digitsinvalid digit in integer literalinteger literal out of rangeunterminated chara"
    "cter literalempty character literalunknown escape sequenceinvalid character in character"
    " literalunterminated string literalinvalid character in string literalnon-ascii byte in "
    "sourceunexpected characterend of fileinteger literalcharacter literalstring literalexpec"
    "ted , found expected identifierexpression nests too deeplytype nests too deeplyi32u32boo"
    "lu8expected typecomparison operators cannot be chainedexpected expressionblock nests too"
    " deeplyexpression statement must be a call_expected `fn`, `struct`, `enum` or `const`voi"
    "d[]mut []&mut unknown type duplicate top-level name  is a built-in type namestruct field"
    "enum payload cannot have aggregate type  cannot have borrow type duplicate field duplica"
    "te variant const must have type i32, u32 or bool, found const  depends on itself is not "
    "a constantcannot cast from cannot cast to cannot negate cannot apply  to  requires bool "
    "operands requires integer operandsdivision by zero in constant expression is not allowed"
    " in a constant expressionnot a constant expressionduplicate parameter cannot pass aggreg"
    "ate  by value is storage-only; use cannot return aggregate cannot return a slice (wasm 1"
    ".0 has a single result)cannot return a borrow is already declared in this scope shadows "
    "a top-level namefunction  must return a value on every pathcannot assign to an immutable"
    " place requires an integer, found , found no value is storage-only; there are no u8 loca"
    "lsa borrow cannot be bound to a locallocal cannot have type  outside of a loopfunction h"
    "as no return type requires an enum, found  has no variant duplicate arm for variant  tak"
    "es  binding(s), found non-exhaustive match: missing  must be the last armcannot copy agg"
    "regate unknown struct  has  field(s)expected field  value(s)unknown name :  and  do not "
    "matchshift amount must be cannot compare cannot order  requires  or  argument(s), found "
    " is borrowed mutably and also used in the same argument list is borrowed mutably more th"
    "an oncestruct literal is only allowed as an initializerenum literal is only allowed as a"
    "n initializer, found a borrowcannot borrow an immutable place as `&mut`callee must be a "
    "function nameunknown function  has no field  is a constant, not a place is a type, not a"
    " placecannot index raw pointer dereference requires `unsafe`cannot dereference expected "
    "a place expressionlenptrexpected a raw pointer, found functions are not valuesa borrow m"
    "ay only appear as a call argument"
  )
)
