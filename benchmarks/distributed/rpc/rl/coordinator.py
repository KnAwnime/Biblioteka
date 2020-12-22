import torch
import torch.distributed.rpc as rpc
import matplotlib.pyplot as plt
from torch.distributed.rpc import rpc_async, rpc_sync, remote

from agent import AgentBase
from observer import ObserverBase

import time
import numpy as np

COORDINATOR_NAME = "coordinator"
AGENT_NAME = "agent"
OBSERVER_NAME = "observer{}"

EPISODE_STEPS = 100


class CoordinatorBase:
    def __init__(self, batch_size, batch, state_size, nlayers, out_features):
        self.batch_size = batch_size
        self.batch = batch

        self.agent_rref = None  # Agent RRef
        self.ob_rrefs = []   # Observer RRef

        agent_info = rpc.get_worker_info(AGENT_NAME)
        self.agent_rref = remote(agent_info, AgentBase)

        for rank in range(batch_size):
            ob_info = rpc.get_worker_info(OBSERVER_NAME.format(rank + 2))
            ob_ref = remote(ob_info, ObserverBase)
            self.ob_rrefs.append(ob_ref)

            ob_ref.rpc_sync().set_state(state_size, batch)

        self.agent_rref.rpc_sync().set_world(
            batch_size, state_size, nlayers, out_features, self.batch)

    def run_coordinator(self, episodes, episode_steps, queue):

        agent_latency_final = []
        agent_throughput_final = []

        observer_latency_final = []
        observer_throughput_final = []

        for ep in range(episodes):
            ep_start_time = time.time()

            print(f"Episode {ep} - ", end='')
            # n_steps = int(episode_steps / self.batch_size)

            n_steps = episode_steps
            agent_start_time = time.time()

            futs = []
            for ob_rref in self.ob_rrefs:
                futs.append(ob_rref.rpc_async().run_ob_episode(
                    self.agent_rref, n_steps))

            rets = torch.futures.wait_all(futs)
            agent_latency, agent_throughput = self.agent_rref.rpc_sync().finish_episode(rets)

            self.agent_rref.rpc_sync().reset_metrics()

            agent_latency_final += agent_latency
            agent_throughput_final += agent_throughput

            observer_latency_final += [ret[2] for ret in rets]
            observer_throughput_final += [ret[3] for ret in rets]

            ep_end_time = time.time()
            episode_time = ep_end_time - ep_start_time
            print(round(episode_time, 3))

        observer_latency_final = [t for s in observer_latency_final for t in s]
        observer_throughput_final = [
            t for s in observer_throughput_final for t in s]

        benchmark_metrics = {'agent latency': {}, 'agent throughput': {}, 'observer latency': {}, 'observer throughput': {}}


        print("For batch size {0}".format(self.batch_size))
        print("\nAgent Latency - ", len(agent_latency_final))
        agent_latency_final = sorted(agent_latency_final)
        for p in [50, 75, 90, 95]:
            v = np.percentile(agent_latency_final, p)
            print("p" + str(p) + ":", round(v, 3))
            benchmark_metrics['agent latency'][p] = round(v, 3)

        print("\nAgent Throughput - ", len(agent_throughput_final))
        agent_throughput_final = sorted(agent_throughput_final)
        for p in [50, 75, 90, 95]:
            v = np.percentile(agent_throughput_final, p)
            print("p" + str(p) + ":", int(v))
            benchmark_metrics['agent throughput'][p] = int(v)

        print("\nObserver Latency - ", len(observer_latency_final))
        observer_latency_final = sorted(observer_latency_final)
        for p in [50, 75, 90, 95]:
            v = np.percentile(observer_latency_final, p)
            print("p" + str(p) + ":", round(v, 3))
            benchmark_metrics['observer latency'][p] = round(v, 3)

        print("\nObserver Throughput - ", len(observer_throughput_final))
        observer_throughput_final = sorted(observer_throughput_final)
        for p in [50, 75, 90, 95]:
            v = np.percentile(observer_throughput_final, p)
            print("p" + str(p) + ":", int(v))
            benchmark_metrics['observer throughput'][p] = int(v)

        if queue:
            queue.put(benchmark_metrics)
