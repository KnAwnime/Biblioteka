#pragma once
#include <c10/core/ScalarType.h>
#include <torch/csrc/WindowsTorchApiMacro.h>
#include <vector>

namespace torch {
namespace jit {
namespace tensorexpr {

class Add;
class Sub;
class Mul;
class Div;
class Mod;
class Max;
class Min;
class And;
class Or;
class Xor;
class Lshift;
class Rshift;
class CompareSelect;

#define IMM_DECLARE(Type, Name) class Name##Imm;
AT_FORALL_SCALAR_TYPES_AND2(Bool, Half, IMM_DECLARE);
#undef IMM_DECLARE

class Cast;
class Var;
class Buf;
class Ramp;
class Load;
class For;
class Block;
class Store;
class Broadcast;
class IfThenElse;
class ExprHandle;
class Expr;
class BaseCallNode;
class Intrinsics;
class FunctionCall;
class Allocate;
class Free;
class Let;
class Cond;
class Stmt;
class Term;
class Polynomial;
class RoundOff;
class ReduceOp;
class AtomicAdd;
class SyncThreads;

class TORCH_API IRMutator {
 public:
  virtual ~IRMutator() {}
  virtual const Expr* mutate(const Add* v);
  virtual const Expr* mutate(const Sub* v);
  virtual const Expr* mutate(const Mul* v);
  virtual const Expr* mutate(const Div* v);
  virtual const Expr* mutate(const Mod* v);
  virtual const Expr* mutate(const Max* v);
  virtual const Expr* mutate(const Min* v);
  virtual const Expr* mutate(const And* v);
  virtual const Expr* mutate(const Or* v);
  virtual const Expr* mutate(const Xor* v);
  virtual const Expr* mutate(const Lshift* v);
  virtual const Expr* mutate(const Rshift* v);
  virtual const Expr* mutate(const CompareSelect* v);
#define IMM_MUTATE_DECLARE(Type, Name) \
  virtual const Expr* mutate(const Name##Imm* v);
  AT_FORALL_SCALAR_TYPES_AND2(Bool, Half, IMM_MUTATE_DECLARE);
#undef IMM_MUTATE_DECLARE
  virtual const Expr* mutate(const Cast* v);
  virtual const Expr* mutate(const Var* v);
  virtual const Expr* mutate(const Buf* v);
  virtual const Expr* mutate(const Ramp* v);
  virtual const Expr* mutate(const Load* v);
  virtual const Expr* mutate(const Broadcast* v);
  virtual const Expr* mutate(const IfThenElse* v);

  // BaseCallNode is the base class for all call nodes.
  // For any visitors that only needs the common behavior, only override this
  // function is enough. This is because all derived class handlers will call
  // this function by default.
  // Override the derived class handler only if the logic is more specific to
  // that.
  virtual const Expr* mutate(const BaseCallNode* v);
  virtual const Expr* mutate(const Intrinsics* v);
  virtual const Expr* mutate(const FunctionCall* v);

  virtual const Expr* mutate(const Term* v);
  virtual const Expr* mutate(const Polynomial* v);
  virtual const Expr* mutate(const RoundOff* v);

  virtual const Expr* mutate(const ReduceOp* v);

  virtual Stmt* mutate(const For* v);
  virtual Stmt* mutate(const Block* v);
  virtual Stmt* mutate(const Store* v);
  virtual Stmt* mutate(const AtomicAdd* v);
  virtual Stmt* mutate(const SyncThreads* v);

  virtual Stmt* mutate(const Allocate* v);
  virtual Stmt* mutate(const Free* v);
  virtual Stmt* mutate(const Let* v);
  virtual Stmt* mutate(const Cond* v);

 protected:
  const Expr* DefaultMutator(
      const BaseCallNode* v,
      std::vector<const Expr*>& params);
};

} // namespace tensorexpr
} // namespace jit
} // namespace torch
