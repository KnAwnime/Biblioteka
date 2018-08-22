#pragma once

#include "../ChannelUtils.hpp"
#include "../Registry.h"

#include <string>
#include <stdexcept>
#include <tuple>
#include <unordered_map>

namespace thd {

// InitMethod is an abstract base class for THD initialization methods.
// See InitMethod* files for implementations.
class InitMethod {
 public:
  struct Config {
    struct MasterConfig {
      int listen_socket;
      port_type listen_port;
    };

    struct WorkerConfig {
      std::string master_addr;
      port_type master_port;
    };

    Config() {
      rank = -1;
      world_size = 0;
      public_address = "";
      master.listen_socket = -1;
      master.listen_port = 0;
      worker.master_addr = "";
      worker.master_port = 0;
    }

    rank_type rank;
    rank_type world_size;
    std::string public_address;
    MasterConfig master;
    WorkerConfig worker;

    void validate() {
      if (world_size == 0)
        throw std::logic_error("world_size was not set in config");

      if (rank >= world_size || rank == -1)
        throw std::logic_error("rank was not set in config");

      if (public_address == "")
        throw std::logic_error("public_address was not set in config");

      if (rank == 0) {
        if (master.listen_socket < 0)
          throw std::logic_error("master:listen_socket was not set in config");

        if (master.listen_port <= 0)
          throw std::logic_error("master:listen_port was not set in config");
      } else {
        if (worker.master_addr == "")
          throw std::logic_error("worker:master_addr was not set in config");

        if (worker.master_port <= 0)
          throw std::logic_error("worker:master_port was not set in config");
      }
    }
  };

  explicit InitMethod();

  virtual ~InitMethod();

  virtual Config init(
      std::string argument,
      int world_size_r,
      std::string group_name,
      int assigned_rank) = 0;
};

AT_DECLARE_REGISTRY(InitMethodRegistry, InitMethod);

InitMethod::Config getInitConfig(std::string argument, int world_size = -1,
                                 std::string group_name = "", int rank = -1);

} // namespace thd
