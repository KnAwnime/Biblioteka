#include <torch/csrc/jit/fuser/common/fusion.h>
#include <torch/csrc/jit/fuser/common/ir.h>
#include <torch/csrc/jit/fuser/common/iter_visitor.h>
#include <torch/csrc/jit/fuser/common/type.h>
#include <queue>
#include <deque>
#include <iostream>

#include <torch/csrc/jit/fuser/common/iriostream.h>

namespace torch {
namespace jit {
namespace fuser {

/*
 * Base IterVisitor calls
 */ 

std::vector<Statement*> IterVisitor::next(
    const Fusion* fusion,
    Statement* statement) {
  if (statement->isVal())
    return next(fusion, static_cast<Val*>(statement));
  else if (statement->isExpr())
    return next(fusion, static_cast<Expr*>(statement));
  else
    throw std::runtime_error("Could not detect type in next_dispatch.");
}

std::vector<Statement*> IterVisitor::next(const Fusion* fusion, Val* v) {
  // ensure IterVisitor does not traverse across fusion;
  assert(fusion == v->fusion());
  if (fusion->origin(v))
    return {fusion->origin(v)};
  return {};
}

std::vector<Statement*> IterVisitor::next(const Fusion* fusion, Expr* expr) {
  return {expr->inputs().begin(), expr->inputs().end()};
}

void IterVisitor::traverseFrom(
    const Fusion* const fusion,
    std::vector<Val*> from) {
  std::set<Statement*> visited;
  std::deque<Statement*> to_visit;
  
  std::queue<Val*> outputs_to_visit;
  for (Val* entry : from)
    outputs_to_visit.emplace(entry);

  while (!outputs_to_visit.empty()) {

    if(stopCondition())
      break;

    to_visit.push_front(outputs_to_visit.front());
    outputs_to_visit.pop();
    while (!to_visit.empty()) {

      if(stopCondition())
        break;

      Statement* stmt = to_visit.front();
      std::vector<Statement*> inps = next(fusion, stmt);
      for (auto it = inps.rbegin(); it != inps.rend(); it++){
        Statement* inp = *it;
        if (visited.find(inp) == visited.end()) {
          toVisitCallback(inp);
          to_visit.emplace_front(inp);
        }
      }

      if (to_visit.front() != stmt) {
        continue;
      }

      to_visit.pop_front();
      if (visited.find(stmt) == visited.end()) {
        handle(stmt);
        visited.emplace(stmt);
      }
    }
  }
}

void IterVisitor::traverse(
      const Fusion* const fusion
    , bool from_outputs_only
    , bool breadth_first
    , std::unordered_set<ValType> val_types) {
  if (breadth_first)
    throw std::runtime_error("Not implemented yet.");
  std::set<Statement*> visited;
  std::deque<Statement*> to_visit;

  std::vector<Val*> outputs_to_visit;

  if (from_outputs_only) {
    for (Val* out : fusion->outputs()) {
	  // Traverse from All Values
      if (val_types.size() == 0) {
      	outputs_to_visit.push_back(out);
	  // Traverse from Specific Value Types
      } else {
        if(val_types.find(out->getValType().value()) != val_types.end()) {
          outputs_to_visit.push_back(out);
        }
      }
    }
  // Search for Vals with no uses (output edges)
  } else
    for (Val* it : fusion->vals()) {
      const std::set<Expr*>& uses = fusion->uses(it);
      if (uses.empty()) {
	    // Traverse from All Values
        if (val_types.size() == 0) {
        	outputs_to_visit.push_back(it);
	    // Traverse from Specific Value Types
        } else {
          if(val_types.find(it->getValType().value()) != val_types.end()) {
            outputs_to_visit.push_back(it);
          }
        }
      }
    }

  traverseFrom(fusion, outputs_to_visit);
}

  void IterVisitor::handle(Statement* s) { Statement::dispatch(this, s); }
  void IterVisitor::handle(Expr* e) { Expr::dispatch(this, e); }
  void IterVisitor::handle(Val* v) { Val::dispatch(this, v); }

//Debug function
/*
std::ostream& operator<<(std::ostream& os, std::stack<Val*> vals) {
  os<<"<";
  while(!vals.empty()){
    os<<vals.top();
    vals.pop();
    if(!vals.empty())
      os<<", ";
  }
  return os<<">";
}
*/
void DependencyCheck::handle(Val* val){
  //Debug dependency chain
  if(val->same_as(dependency_))
    is_dependency = true;
}

void DependencyCheck::handle(Expr* expr){
  //We want to update the dependency chain, but we want to make sure
  //that the top value on the chain is an output of this expr
  
  for(decltype(expr->nOutputs()) i = 0; i < expr->nOutputs(); i++){
    TORCH_CHECK(expr->hasOutput(dep_chain.top()));
    dep_chain.pop();
  }
}

void DependencyCheck::toVisitCallback(Statement* stmt){
  //If an expression push outputs of expr to dependency chain.
  if(stmt->isExpr()){
    Expr* expr = static_cast<Expr*>(stmt);
    for(auto out : expr->outputs()){
      dep_chain.push(static_cast<Val*>(out));
    }
  }
}

bool DependencyCheck::check(){
  is_dependency = false;
  IterVisitor::traverseFrom(of_->fusion(), {of_});
  return is_dependency;
}

std::stack<Val*> DependencyCheck::getDependencyChain(Val* dependency, Val* of) {
  DependencyCheck dp(dependency, of);
  dp.check();

  //Return the reversed stack, we start from output and go to the input, including of, but not dependency
  std::stack<Val*> dep_copy = dp.dep_chain;
  std::stack<Val*> reversed_clean;

  while(!dep_copy.empty()){
    Val* next = dep_copy.top(); dep_copy.pop();
      reversed_clean.push(next);
  }
  return reversed_clean;
}

} // namespace fuser
} // namespace jit
} // namespace torch
