# Copyright 2012-2013 Greg Horn
#
# This file is part of rawesome.
#
# rawesome is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# rawesome is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with rawesome.  If not, see <http://www.gnu.org/licenses/>.

import sys

import rawe
import rocket_dae

if __name__=='__main__':
    assert len(sys.argv)==3
    topname = sys.argv[1]
    autogenDir = sys.argv[2]
    dae = rocket_dae.makeDae()
    rawe.utils.mkprotobufs.writeAll(dae, topname, autogenDir)
