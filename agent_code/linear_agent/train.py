from collections import namedtuple, deque

import numpy as np
import os
import pickle
from typing import List
from .constants import *

from .callbacks import state_to_features, get_bomb_explosion_fields

# This is only an example!
Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward'))

# Hyper parameters -- DO modify
TRANSITION_HISTORY_SIZE = 3  # keep only ... last transitions
RECORD_ENEMY_TRANSITIONS = 1.0  # record enemy transitions with probability ...


def reset_lists(self):
    self.q_updates = []
    self.features = []
    self.actions = []
    self.errors = []


def check_model_update(self):
    if len(self.q_updates) < self.config['batch_size']:
        return

    N_batch = len(self.q_updates)
    probabilities = np.clip(np.array(self.errors), 0, self.config['per']['error_clip']) + self.config['per']['error_offset']
    probabilities = np.power(probabilities, self.config['per']['priority_scale'])
    probabilities /= probabilities.sum()
    #print(self.errors)
    selection = np.random.choice(N_batch, size=self.config['n_training_per_batch'], replace=False, p=probabilities)
    q_updates_np = np.array(self.q_updates)[selection]
    Fnp = np.array(self.features)[selection]
    actions_np = np.array(self.actions)[selection]
    importance_weight = np.power(probabilities[selection], -self.config['per']['b'])
    for action_index, action in enumerate(ACTIONS):
        action_mask = actions_np == action
        if not np.any(action_mask):
            continue
        
        q_updates_masked = q_updates_np[action_mask]
        Fmasked = Fnp[action_mask]
        importance_weight_masked = importance_weight[action_mask]
        self.model.T[action_index] += self.config['learning_rate'] * \
            ((importance_weight_masked * Fmasked.T) @ \
            (q_updates_masked - Fmasked @ self.model.T[action_index]))

    reset_lists(self)
    with open(self.config['model_filename'], "wb") as file:
        pickle.dump(self.model, file)


def setup_training(self):
    self.transitions = deque(maxlen=TRANSITION_HISTORY_SIZE)
    reset_lists(self)
    self.waited_counter = 0
    self.invalid_counter = 0
    self.round_counter = 0


def add_custom_events(self, old_game_state: dict, action: str, events: List[str]):
    action_index = ACTIONS.index(action)
    state_vector = state_to_features(old_game_state)

    coin_pos_features = state_vector[COIN_POS_FSTART : COIN_POS_FEND]
    crate_pos_features = state_vector[CRATE_POS_FSTART : CRATE_POS_FEND]
    live_saving_features = state_vector[LIVE_SAVING_FSTART : LIVE_SAVING_FEND]
    deadly_features = state_vector[DEADLY_FSTART : DEADLY_FEND]
    bomb_survivable_feature = state_vector[BOMB_SURVIVABLE_FSTART : BOMB_SURVIVABLE_FEND]
    bomb_crate_in_range_feature = state_vector[BOMB_CRATE_IN_RANGE_FSTART : BOMB_CRATE_IN_RANGE_FEND]
    bomb_others_in_range_feature = state_vector[BOMB_OTHERS_IN_RANGE_FSTART : BOMB_OTHERS_IN_RANGE_FEND]

    def feature_action_picked(feature_vector):
        return action_index < len(feature_vector) and feature_vector[action_index] != 0

    if np.any(deadly_features): 
        if feature_action_picked(deadly_features):
            events.append(DEADLY_MOVE_CHOOSEN)
            return
        else:
            events.append(DEADLY_MOVE_AVOIDED)
    
    if np.any(live_saving_features != 0):
        if feature_action_picked(live_saving_features):
            events.append(LIVE_SAVING_MOVE_CHOOSEN)
        else:
            events.append(LIVE_SAVING_MOVE_AVOIDED)
        return
    
    if bomb_survivable_feature[0] == 0:
        if action == 'BOMB':
            events.append(UNSURVIVABLE_BOMB_CHOOSEN)
            return
        else:
            events.append(SURVIVABLE_BOMB_CHOOSEN)
    
    if action == 'BOMB':
        #explosion_fields = get_bomb_explosion_fields(old_game_state['self'][3], old_game_state['field'])
        #if contains_crate(explosion_fields, old_game_state['field']):
        #    events.append(USEFULL_BOMB)
        #else:
        #    events.append(USELESS_BOMB)
        
        if bomb_crate_in_range_feature[0] != 0 or bomb_others_in_range_feature[0] != 0:
            events.append(USEFULL_BOMB)
        else:
            events.append(USELESS_BOMB)
    
    if np.any(coin_pos_features != 0):
        if feature_action_picked(coin_pos_features):
            events.append(COIN_MOVE_CHOOSEN)
        else:
            events.append(COIN_MOVE_AVOIDED)
        return
    
    if np.any(crate_pos_features != 0):
        if feature_action_picked(crate_pos_features):
            events.append(CRATE_MOVE_CHOOSEN)
        else:
            events.append(CRATE_MOVE_AVOIDED)
        return

    #if e.WAITED in events and np.all(old_game_state['explosion_map'] == 0):
    #    events.append(UNNECESSARY_WAITING)    


def game_events_occurred(self, old_game_state: dict, self_action: str, new_game_state: dict, events: List[str]):
    self.logger.debug(f'Encountered game event(s) {", ".join(map(repr, events))} in step {new_game_state["step"]}')
    
    add_custom_events(self, old_game_state, self_action, events)

    # state_to_features is defined in callbacks.py
    old_features = state_to_features(old_game_state)
    new_features = state_to_features(new_game_state)
    reward = reward_from_events(self, events)
    self.transitions.append(Transition(old_features, self_action, new_features, reward))

    q_vector = self.model.T @ new_features
    q_update = reward + self.config['gamma'] * np.max(q_vector)
    self.q_updates.append(q_update)
    self.features.append(old_features)
    self.actions.append(self_action)
    self.errors.append(np.abs(q_update - (self.model.T @ old_features)[ACTIONS.index(self_action)]))

    check_model_update(self)


def end_of_round(self, last_game_state: dict, last_action: str, events: List[str]):
    old_features = state_to_features(last_game_state)
    reward = reward_from_events(self, events)
    self.logger.debug(f'Encountered event(s) {", ".join(map(repr, events))} in final step')
    self.transitions.append(Transition(old_features, last_action, None, reward))

    q_update = reward
    self.q_updates.append(q_update)
    self.features.append(old_features)
    self.actions.append(last_action)
    self.errors.append(np.abs(q_update - (self.model.T @ old_features)[ACTIONS.index(last_action)]))

    check_model_update(self)

    self.round_counter += 1


def reward_from_events(self, events: List[str]) -> int:
    game_rewards = self.config['rewards']
    reward_sum = 0
    for event in events:
        if event in game_rewards:
            reward_sum += game_rewards[event]
    self.logger.info(f"Awarded {reward_sum} for events {', '.join(events)}")
    return reward_sum
