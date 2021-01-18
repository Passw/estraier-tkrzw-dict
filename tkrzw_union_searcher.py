#! /usr/bin/python3
# -*- coding: utf-8 -*-
#--------------------------------------------------------------------------------------------------
# Dictionary searcher of union database
#
# Copyright 2020 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file
# except in compliance with the License.  You may obtain a copy of the License at
#     https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied.  See the License for the specific language governing permissions
# and limitations under the License.
#--------------------------------------------------------------------------------------------------

import collections
import heapq
import json
import math
import operator
import regex
import tkrzw
import tkrzw_dict


class UnionSearcher:
  def __init__(self, data_prefix):
    body_path = data_prefix + "-body.tkh"
    self.body_dbm = tkrzw.DBM()
    self.body_dbm.Open(body_path, False, dbm="HashDBM").OrDie()
    tran_index_path = data_prefix + "-tran-index.tkh"
    self.tran_index_dbm = tkrzw.DBM()
    self.tran_index_dbm.Open(tran_index_path, False, dbm="HashDBM").OrDie()
    infl_index_path = data_prefix + "-infl-index.tkh"
    self.infl_index_dbm = tkrzw.DBM()
    self.infl_index_dbm.Open(infl_index_path, False, dbm="HashDBM").OrDie()
    keys_path = data_prefix + "-keys.txt"
    self.keys_file = tkrzw.TextFile()
    self.keys_file.Open(keys_path).OrDie()
    tran_keys_path = data_prefix + "-tran-keys.txt"
    self.tran_keys_file = tkrzw.TextFile()
    self.tran_keys_file.Open(tran_keys_path).OrDie()

  def __del__(self):
    self.tran_index_dbm.Close().OrDie()
    self.body_dbm.Close().OrDie()

  def SearchBody(self, text):
    serialized = self.body_dbm.GetStr(text)
    if not serialized:
      return None
    return json.loads(serialized)

  def SearchTranIndex(self, text):
    text = tkrzw_dict.NormalizeWord(text)
    tsv = self.tran_index_dbm.GetStr(text)
    result = []
    if tsv:
      result.extend(tsv.split("\t"))
    return result

  def GetResultKeys(self, entries):
    keys = set()
    for entry in entries:
      keys.add(tkrzw_dict.NormalizeWord(entry["word"]))
    return keys

  def SearchInflections(self, text):
    result = []
    tsv = self.infl_index_dbm.GetStr(text)
    if tsv:
      result.extend(tsv.split("\t"))
    return result

  def SearchExact(self, text, capacity):
    result = []
    uniq_words = set()
    for word in text.split(","):
      if len(result) >= capacity: break
      word = tkrzw_dict.NormalizeWord(word)
      if not word: continue
      entries = self.SearchBody(word)
      if not entries: continue
      for entry in entries:
        if len(result) >= capacity: break
        word = entry["word"]
        if word in uniq_words: continue
        uniq_words.add(word)
        result.append(entry)
    return result

  def SearchExactReverse(self, text, capacity):
    ja_words = []
    ja_uniq_words = set()
    for ja_word in text.split(","):
      ja_word = tkrzw_dict.NormalizeWord(ja_word)
      if not ja_word: continue
      if ja_word in ja_uniq_words: continue
      ja_uniq_words.add(ja_word)
      ja_words.append(ja_word)
    en_words = []
    en_uniq_words = set()
    for ja_word in ja_words:
      for en_word in self.SearchTranIndex(ja_word):
        if en_word in en_uniq_words: continue
        en_uniq_words.add(en_word)
        en_words.append(en_word)
    result = []
    uniq_words = set()
    for en_word in en_words:
      if capacity < 1: break
      entries = self.SearchBody(en_word)
      if entries:
        for entry in entries:
          if capacity < 1: break
          word = entry["word"]
          if word in uniq_words: continue
          uniq_words.add(word)
          match = False
          translations = entry.get("translation")
          if translations:
            for tran in translations:
              tran = tkrzw_dict.NormalizeWord(tran)
              for ja_word in ja_words:
                if tran.find(ja_word) >= 0:
                  match = True
                  break
              if match: break
          if match:
            result.append(entry)
            capacity -= 1
    return result

  def ExpandEntries(self, seed_entries, seed_features, capacity):
    result = []
    seeds = []
    num_steps = 0
    def AddSeed(entry):
      nonlocal num_steps
      features = self.GetFeatures(entry)
      score = self.GetSimilarity(seed_features, features)
      heapq.heappush(seeds, (-score, num_steps, entry))
      num_steps += 1
    checked_words = set()
    checked_trans = set()
    for entry in seed_entries:
      word = entry["word"]
      if word in checked_words: continue
      checked_words.add(word)
      AddSeed(entry)
    while seeds:
      score, cur_steps, entry = heapq.heappop(seeds)
      score *= -1
      result.append(entry)
      num_appends = 0
      max_rel_words = 16 / math.log2(len(result) + 1) * score
      max_trans = 8 / math.log2(len(result) + 1) * score
      max_rel_words = max(int(max_rel_words), 4)
      max_trans = max(int(max_trans), 2)
      rel_words = []
      for i, rel_name in enumerate(("related", "parent", "child")):
        tmp_rel_words = entry.get(rel_name)
        if tmp_rel_words:
          for j, rel_word in enumerate(tmp_rel_words):
            rel_words.append((rel_word, i + j))
      if rel_words:
        rel_words = sorted(rel_words, key=lambda x: x[1])
        rel_words = [x[0] for x in rel_words]
        for rel_word in rel_words[:max_rel_words]:
          if len(checked_words) >= capacity: break
          if rel_word in checked_words: continue
          for child in self.SearchExact(rel_word, capacity - len(checked_words)):
            if len(checked_words) >= capacity: break
            word = child["word"]
            if word in checked_words: continue
            checked_words.add(word)
            AddSeed(child)
            num_appends += 1
      trans = entry.get("translation")
      if trans:
        for tran in trans[:max_trans]:
          if len(checked_words) >= capacity: break
          tran = regex.sub(
            r"([\p{Han}\p{Katakana}ー]{2,})(する|すること|される|されること|をする)$",
            r"\1", tran)
          tran = regex.sub(
            r"([\p{Han}\p{Katakana}ー]{2,})(的|的な|的に)$",
            r"\1", tran)
          if tran in checked_trans: continue
          checked_trans.add(tran)
          max_children = min(capacity - len(checked_words), 10)
          num_tran_adopts = 0
          for child in self.SearchExactReverse(tran, max_children):
            if len(checked_words) >= capacity: break
            if num_tran_adopts >= 5: break
            word = child["word"]
            if word in checked_words: continue
            checked_words.add(word)
            AddSeed(child)
            num_tran_adopts += 1
            num_appends += 1
      coocs = entry.get("cooccurrence")
      if coocs:
        for cooc in coocs:
          if num_appends >= 8: break
          if len(checked_words) >= capacity: break
          if cooc in checked_words: continue
          for child in self.SearchExact(cooc, capacity - len(checked_words)):
            if len(checked_words) >= capacity: break
            word = child["word"]
            if word in checked_words: continue
            checked_words.add(word)
            AddSeed(child)
            num_appends += 1
    return result

  def GetFeatures(self, entry):
    SCORE_DECAY = 0.95
    word = tkrzw_dict.NormalizeWord(entry["word"])
    features = {word: 1.0}
    pos_score = 1.0
    pos_score_max = 0.0
    pos_features = collections.defaultdict(float)
    for item in entry["item"]:
      pos = "__" + item["pos"]
      new_score = (pos_features.get(pos) or 0.0) + pos_score
      pos_features[pos] = new_score
      pos_score_max = max(pos_score_max, new_score)
      pos_score *= SCORE_DECAY
    for pos, pos_feature_score in pos_features.items():
      features[pos] = pos_feature_score / pos_score_max
    score = 1.0
    rel_words = entry.get("related")
    if rel_words:
      for rel_word in rel_words[:20]:
        rel_word = tkrzw_dict.NormalizeWord(rel_word)
        if rel_word not in features:
          score *= SCORE_DECAY
          features[rel_word] = score
    trans = entry.get("translation")
    if trans:
      for tran in trans[:20]:
        tran = tkrzw_dict.NormalizeWord(tran)
        tran = regex.sub(
          r"([\p{Han}\p{Katakana}ー]{2,})(する|すること|される|されること|をする|な|に|さ)$",
          r"\1", tran)
        if tran not in features:
          score *= SCORE_DECAY
          features[tran] = score
    coocs = entry.get("cooccurrence")
    if coocs:
      for cooc in coocs[:20]:
        cooc = tkrzw_dict.NormalizeWord(cooc)
        if cooc not in features:
          score *= SCORE_DECAY
          features[cooc] = score
    return features

  def GetSimilarity(self, seed_features, cand_features):
    seed_norm, cand_norm = 0.0, 0.0
    product = 0.0
    for seed_word, seed_score in seed_features.items():
      cand_score = cand_features.get(seed_word) or 0.0
      product += seed_score * cand_score
      seed_norm += seed_score ** 2
      cand_norm += cand_score ** 2
    if cand_norm == 0 or seed_norm == 0: return 0.0
    score = min(product / ((seed_norm ** 0.5) * (cand_norm ** 0.5)), 1.0)
    if score >= 0.99999: score = 1.0
    return score

  def SearchRelatedWithSeeds(self, seeds, capacity):
    seed_features = collections.defaultdict(float)
    base_weight = 1.0
    uniq_words = set()
    for seed in seeds:
      norm_word = tkrzw_dict.NormalizeWord(seed["word"])
      weight = base_weight
      if norm_word in uniq_words:
        weight *= 0.1
      uniq_words.add(norm_word)
      for word, score in self.GetFeatures(seed).items():
        seed_features[word] += score * weight
      base_weight *= 0.8
    result = self.ExpandEntries(seeds, seed_features, max(int(capacity * 1.2), 100))
    return result[:capacity]

  def SearchRelated(self, text, capacity):
    seeds = []
    words = text.split(",")
    for word in words:
      if word:
        seeds.extend(self.SearchExact(word, capacity))
    return self.SearchRelatedWithSeeds(seeds, capacity)

  def SearchRelatedReverse(self, text, capacity):
    seeds = []
    words = text.split(",")
    for word in words:
      if word:
        seeds.extend(self.SearchExactReverse(word, capacity))
    return self.SearchRelatedWithSeeds(seeds, capacity)

  def SearchPatternMatch(self, mode, text, capacity):
    text = tkrzw_dict.NormalizeWord(text)
    keys = self.keys_file.Search(mode, text, capacity, True)
    result = []
    for key in keys:
      if len(result) >= capacity: break
      for entry in self.SearchExact(key, capacity - len(result)):
        result.append(entry)
    return result

  def SearchPatternMatchReverse(self, mode, text, capacity):
    text = tkrzw_dict.NormalizeWord(text)
    keys = self.tran_keys_file.Search(mode, text, capacity, True)
    result = []
    uniq_words = set()
    for key in keys:
      if len(result) >= capacity: break
      for entry in self.SearchExactReverse(key, capacity - len(result) + 10):
        if len(result) >= capacity: break
        word = entry["word"]
        if word in uniq_words: continue
        uniq_words.add(word)
        result.append(entry)
    return result

  def SearchByGrade(self, capacity, page, first_only):
    keys = self.keys_file.Search("begin", "", capacity * page, False)
    if page > 1:
      skip = capacity * (page - 1)
      keys = keys[skip:]
    result = []
    for key in keys:
      if len(result) >= capacity: break
      for entry in self.SearchExact(key, capacity - len(result)):
        result.append(entry)
        if first_only:
          break
    return result
