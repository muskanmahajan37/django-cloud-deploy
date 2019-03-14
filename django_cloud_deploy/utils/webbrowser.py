# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Webbrowser utility functions."""

import os
import webbrowser

def open_url(url: str):
    """Filter ugly terminal output with webbrowser"""
    with open(os.devnull, 'wb') as f:
        os.dup2(f.fileno(), 2)
        webbrowser.open(url)
